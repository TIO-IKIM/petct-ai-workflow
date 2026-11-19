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
