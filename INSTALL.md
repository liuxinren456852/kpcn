### Installation instructions for Ubuntu 16.04
     
* Make sure <a href="https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html">CUDA</a>  and <a href="https://docs.nvidia.com/deeplearning/sdk/cudnn-install/index.html">cuDNN</a> are installed. Three configurations have been tested: 
     - TensorFlow 1.4.1, CUDA 8.0 and cuDNN 6.0
     - TensorFlow 1.12.0, CUDA 9.0 and cuDNN 7.3.1, gcc/g++ 4.8, Python 3.6.9
     - TensorFlow 1.12.0, CUDA 9.0 and cuDNN 7.4
     - ~~TensorFlow 1.13.0, CUDA 10.0 and cuDNN 7.5~~ (bug found only with this version).

* Tested on a RTX 2080 Ti. Driver version: 450.80.02
     
* Ensure all python packages are installed :

          sudo apt update
          sudo apt install python3-dev python3-pip python3-tk

* Follow <a href="https://www.tensorflow.org/install/pip">Tensorflow installation procedure</a>.

* Install the other dependencies with pip:
     - numpy
     - scikit-learn
     - psutil
     - matplotlib (for visualization)
     - mayavi (for visualization)
     - PyQt5 (for visualization)
     - Open3D (for point cloud I/O)
     - bpy (for rendering depth images via blender)
     - OpenEXR & Imath
     - h5py==2.9.0
     - pandas==0.24.2
     - transforms3d==0.3.1
     - seaborn
     
* Build the distance cuda kernels in `pc_distance`. Open a terminal in this folder, and run:

          make

* Compile the customized Tensorflow operators located in `tf_custom_ops`. Open a terminal in this folder, and run:

          sh compile_op.sh

     N.B. If you installed Tensorflow in a virtual environment, it needs to be activated when running these scripts
     
* Compile the C++ extension module for python located in `cpp_wrappers`. Open a terminal in this folder, and run:

          sh compile_wrappers.sh

You should now be able to train Kernel-Point Convolution models

### Installation instructions for Ubuntu 18.04 (Thank to @noahtren)

* Remove the `-D_GLIBCXX_USE_CXX11_ABI=0` flag for each line in `tf_custom_ops/compile_op.sh` (problem with the version of gcc). One configuration has been tested:

     - TensorFlow 1.12.0, CUDA 9.0 and cuDNN 7.3.1
