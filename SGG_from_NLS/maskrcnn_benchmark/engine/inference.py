# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
import logging
import time
import os

import json
import torch
from tqdm import tqdm

from maskrcnn_benchmark.config import cfg
from maskrcnn_benchmark.data.datasets.evaluation import evaluate
from ..utils.comm import is_main_process, get_world_size
from ..utils.comm import all_gather
from ..utils.comm import synchronize
from ..utils.timer import Timer, get_time_str
from .bbox_aug import im_detect_bbox_aug


def compute_on_dataset(model, data_loader, device, synchronize_gather=True, timer=None, offline_od=False, use_uniter=False):
    model.eval()
    results_dict = {}
    cpu_device = torch.device("cpu")
    torch.cuda.empty_cache()
    for _, batch in enumerate(tqdm(data_loader)):
        with torch.no_grad():
            # offline_od = False # Delete
            if offline_od: # offline object detector
                if use_uniter:
                    det_feats, det_dists, det_boxes, targets, image_ids, images, det_tag_ids, det_norm_pos = batch
                    targets = [target.to(device) for target in targets]
                    det_feats = [det_feat.to(device) for det_feat in det_feats]
                    det_dists = [det_dist.to(device) for det_dist in det_dists]
                    det_boxes = [det_box.to(device) for det_box in det_boxes] 
                    det_tag_ids = [[det_tag_i.to(device) for det_tag_i in det_tag_id] for det_tag_id in det_tag_ids]
                    det_norm_pos = [det_norm_p.to(device) for det_norm_p in det_norm_pos] 
                else:
                    det_feats, det_dists, det_boxes, targets, image_ids, images = batch
                    targets = [target.to(device) for target in targets]
                    det_feats = [det_feat.to(device) for det_feat in det_feats]
                    det_dists = [det_dist.to(device) for det_dist in det_dists]
                    det_boxes = [det_box.to(device) for det_box in det_boxes]   
            else: # online object detector
                images, targets, image_ids = batch
                targets = [target.to(device) for target in targets]    

            if timer:
                timer.tic()
            if cfg.TEST.BBOX_AUG.ENABLED:
                output = im_detect_bbox_aug(model, images, device)
            else:
                # relation detection needs the targets
                if offline_od:  # offline object detector
                    print("offline_od ONLINE OBJECT")
                    if use_uniter:
                        output = model(images.to(device), targets, det_feats=det_feats, det_dists=det_dists, det_boxes=det_boxes, \
                                    det_tag_ids=det_tag_ids, det_norm_pos=det_norm_pos)
                        # print("OUTPUT: ", output)#OUTPUT:  [BoxList(num_boxes=4, image_width=800, image_height=600, mode=xyxy), BoxList(num_boxes=2, image_width=800, image_height=600, mode=xyxy)]
                    else:
                        output = model(images.to(device), targets, det_feats=det_feats, det_dists=det_dists, det_boxes=det_boxes)
                else:  # online object detector
                    output = model(images.to(device), targets)

            if timer:
                if not cfg.MODEL.DEVICE == 'cpu':
                    torch.cuda.synchronize()
                timer.toc()
            output = [o.to(cpu_device) for o in output]
        if synchronize_gather:
            synchronize()
            multi_gpu_predictions = all_gather({img_id: result for img_id, result in zip(image_ids, output)})
            if is_main_process():
                for p in multi_gpu_predictions:
                    results_dict.update(p)
        else:
            results_dict.update(
                {img_id: result for img_id, result in zip(image_ids, output)}
            )
    torch.cuda.empty_cache()
    return results_dict


def _accumulate_predictions_from_multiple_gpus(predictions_per_gpu, synchronize_gather=True):
    if not synchronize_gather:
        all_predictions = all_gather(predictions_per_gpu)
    if not is_main_process():
        return

    if synchronize_gather:
        predictions = predictions_per_gpu
    else:
        # merge the list of dicts
        predictions = {}
        for p in all_predictions:
            predictions.update(p)
    
    # convert a dict where the key is the index in a list
    image_ids = list(sorted(predictions.keys()))
    if len(image_ids) != image_ids[-1] + 1:
        logger = logging.getLogger("maskrcnn_benchmark.inference")
        logger.warning(
            "WARNING! WARNING! WARNING! WARNING! WARNING! WARNING!"
            "Number of images that were gathered from multiple processes is not "
            "a contiguous set. Some images might be missing from the evaluation"
        )

    # convert to a list
    predictions = [predictions[i] for i in image_ids]
    return predictions


def inference(
        cfg,
        model,
        data_loader,
        dataset_name,
        iou_types=("bbox",),
        box_only=False,
        device="cuda",
        expected_results=(),
        expected_results_sigma_tol=4,
        output_folder=None,
        logger=None,
        offline_od=False,
        use_uniter=False
):
    load_prediction_from_cache = cfg.TEST.ALLOW_LOAD_FROM_CACHE and output_folder is not None and os.path.exists(os.path.join(output_folder, "eval_results.pytorch"))
    # convert to a torch.device for efficiency
    device = torch.device(device)
    num_devices = get_world_size()
    if logger is None:
        logger = logging.getLogger("maskrcnn_benchmark.inference")
    dataset = data_loader.dataset


    logger.info("Start evaluation on {} dataset({} images).".format(dataset_name, len(dataset)))
    total_timer = Timer()
    inference_timer = Timer()
    total_timer.tic()

    # print("******>>>> load_prediction_from_cache: ", load_prediction_from_cache)
    if load_prediction_from_cache:
        predictions = torch.load(os.path.join(output_folder, "eval_results.pytorch"), map_location=torch.device("cpu"))['predictions']
    else:
        predictions = compute_on_dataset(model, data_loader, device, synchronize_gather=cfg.TEST.RELATION.SYNC_GATHER, timer=inference_timer, \
            offline_od=offline_od, use_uniter=use_uniter)

    # wait for all processes to complete before measuring the time
    synchronize()
    total_time = total_timer.toc()
    total_time_str = get_time_str(total_time)
    logger.info(
        "Total run time: {} ({} s / img per device, on {} devices)".format(
            total_time_str, total_time * num_devices / len(dataset), num_devices
        )
    )
    total_infer_time = get_time_str(inference_timer.total_time)
    logger.info(
        "Model inference time: {} ({} s / img per device, on {} devices)".format(
            total_infer_time,
            inference_timer.total_time * num_devices / len(dataset),
            num_devices,
        )
    )

    if not load_prediction_from_cache:
        predictions = _accumulate_predictions_from_multiple_gpus(predictions, synchronize_gather=cfg.TEST.RELATION.SYNC_GATHER)

    if not is_main_process():
        return -1.0

    # if output_folder is not None and not load_prediction_from_cache and cfg.WSVL.USE_CAP_TRIP and cfg.WSVL.SKIP_TRAIN:
    #     torch.save(predictions, os.path.join(output_folder, "predictions.pth"))

    extra_args = dict(
        box_only=box_only,
        iou_types=iou_types,
        expected_results=expected_results,
        expected_results_sigma_tol=expected_results_sigma_tol,
    )

    print("Debugging: ", cfg.TEST.CUSTUM_EVAL, len(predictions))
    if cfg.TEST.CUSTUM_EVAL or (cfg.WSVL.SKIP_TRAIN and len(predictions) < 500):
        detected_sgg = custom_sgg_post_precessing(predictions, save_all_scores=False)
        json_file_name = 'visualization/custom_prediction.json'

        with open(os.path.join(cfg.DETECTED_SGG_DIR, json_file_name), 'w') as outfile:  
            json.dump(detected_sgg, outfile)
        # print('=====> ' + str(os.path.join(cfg.DETECTED_SGG_DIR, json_file_name)) + ' SAVED !')
        return -1.0

    return evaluate(cfg=cfg,
                    dataset=dataset,
                    predictions=predictions,
                    output_folder=output_folder,
                    logger=logger,
                    **extra_args)



def custom_sgg_post_precessing(predictions, save_all_scores=True):
    output_dict = {}

    for idx, boxlist in enumerate(predictions):
        xyxy_bbox = boxlist.convert('xyxy').bbox

        # current sgg info
        current_dict = {}

        # sort bbox based on confidence
        sortedid, id2sorted = get_sorted_bbox_mapping(boxlist.get_field('pred_scores').tolist())

        # sorted bbox label and score
        bbox = []
        bbox_labels = []
        bbox_scores = []
        for i in sortedid:
            bbox.append(xyxy_bbox[i].tolist())
            bbox_labels.append(boxlist.get_field('pred_labels')[i].item())
            bbox_scores.append(boxlist.get_field('pred_scores')[i].item())
        current_dict['bbox'] = bbox
        current_dict['bbox_labels'] = bbox_labels
        current_dict['bbox_scores'] = bbox_scores

        # sorted relationships  (remember to change the rel_class_prob into 2-dim instead of the scores)
        rel_sortedid, _ = get_sorted_bbox_mapping(boxlist.get_field('pred_rel_scores')[:,1:].max(1)[0].tolist())
        # sorted rel
        rel_pairs = []
        rel_labels = []
        rel_scores = []
        rel_all_scores = []
        
        for i in rel_sortedid:
            rel_labels.append(boxlist.get_field('pred_rel_scores')[i][1:].max(0)[1].item() + 1)
            rel_scores.append(boxlist.get_field('pred_rel_scores')[i][1:].max(0)[0].item())
            rel_all_scores.append(boxlist.get_field('pred_rel_scores')[i].tolist())
            old_pair = boxlist.get_field('rel_pair_idxs')[i].long().tolist()
            rel_pairs.append([id2sorted[old_pair[0]], id2sorted[old_pair[1]]])
        current_dict['rel_pairs'] = rel_pairs
        current_dict['rel_labels'] = rel_labels
        current_dict['rel_scores'] = rel_scores
        current_dict['rel_all_scores'] = rel_all_scores if save_all_scores else None
        output_dict[idx] = current_dict

    return output_dict
    
def get_sorted_bbox_mapping(score_list):
    sorted_scoreidx = sorted([(s, i) for i, s in enumerate(score_list)], reverse=True)
    sorted2id = [item[1] for item in sorted_scoreidx]
    id2sorted = [item[1] for item in sorted([(j,i) for i, j in enumerate(sorted2id)])]
    return sorted2id, id2sorted