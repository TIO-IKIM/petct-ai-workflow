# pet-segmentation v1: FDG-avid lesion segmentation with nnU-Net v2 (ResEnc M, 3d_fullres).
# classify_pet.py picks the model (FDG or PSMA) from the PET with tracer_classifier.pt.
# Input:  <input>/petct_0000.nii.gz (CT on the PET grid) and petct_0001.nii.gz (PET in SUV)
# Output: <output>/tumor_seg.nii.gz (voxels below SUV 1.5 for FDG, 1.0 for PSMA are removed)
#
# Model weights are not in this repository (about 7.7 GB). Before building, place them here:
#   nnUNet_results/Dataset001_fdgweighted/nnUNetTrainer_1500epochs_fasterdec__nnUNetResEncUNetMPlans__3d_fullres/
#   nnUNet_results/Dataset002_psmaweighted/nnUNetTrainer__nnUNetResEncUNetMPlans__3d_fullres/
#   tracer_classifier.pt
# Build: docker build --platform linux/amd64 -t negarshahamiri/pet-segmentation:v1 microservices/pet-segmentation
FROM pytorch/pytorch:2.2.1-cuda12.1-cudnn8-runtime

WORKDIR /opt/algorithm
ENV PATH=/home/algorithm/.local/bin:$PATH

COPY nnUNet /opt/algorithm/nnUNet/
COPY requirements.txt process.py classify_pet.py tracer_classifier.pt /opt/algorithm/
COPY nnUNet_results /opt/algorithm/nnUNet_results

RUN python -m pip install -U pip && \
    python -m pip install -e /opt/algorithm/nnUNet/ && \
    python -m pip install -r requirements.txt && \
    mkdir -p /opt/algorithm/nnUNet_raw /opt/algorithm/nnUNet_preprocessed /opt/algorithm/prediction

ENV nnUNet_raw=/opt/algorithm/nnUNet_raw \
    nnUNet_preprocessed=/opt/algorithm/nnUNet_preprocessed \
    nnUNet_results=/opt/algorithm/nnUNet_results
