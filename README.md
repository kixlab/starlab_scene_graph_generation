# Scene Graph Generation from how-to video
This interface builds on top of the SGG_from_NLS code. 

### Step-by-step installation

```bash
# first, make sure that your conda is setup properly with the right environment
# for that, check that `which conda`, `which pip` and `which python` points to the
# right path. From a clean conda env, this is what you need to do

conda create --name scene_graph_generation python==3.7.15
conda activate scene_graph_generation

# this installs the right pip and dependencies for the fresh python
conda install ipython scipy h5py

# scene_graph_benchmark and coco api dependencies
pip install ninja yacs cython matplotlib tqdm opencv-python overrides

# follow PyTorch installation in https://pytorch.org/get-started/locally/
# we give the instructions for CUDA 10.1
conda install pytorch=1.6.0 torchvision=0.7.0 cudatoolkit=10.1 -c pytorch

export INSTALL_DIR=$PWD

# install pycocotools
cd $INSTALL_DIR
git clone https://github.com/cocodataset/cocoapi.git
cd cocoapi/PythonAPI
python setup.py build_ext install

find /usr/local/ -name cublas_v2.h
export CPLUS_INCLUDE_PATH=$CPLUS_INCLUDE_PATH:[YOUR PATH]

# install apex
cd $INSTALL_DIR
# clone the previous version of apex, not a current version.
git clone -b 22.04-dev --single-branch https://github.com/NVIDIA/apex.git
cd apex

python setup.py install --cuda_ext --cpp_ext

# install PyTorch Detection
cd $INSTALL_DIR
git clone https://github.com/YiwuZhong/SGG_from_NLS.git
cd SGG_from_NLS

# the following will install the lib with
# symbolic links, so that you can modify
# the files if you want and won't need to
# re-build it
python setup.py build develop

unset INSTALL_DIR

