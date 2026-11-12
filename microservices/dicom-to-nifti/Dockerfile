# dicom-to-nifti v2: universal DICOM series -> NIfTI (CT in HU, PET as SUVbw).
# Build: docker build --platform linux/amd64 -t negarshahamiri/dicom-to-nifti:v2 microservices/dicom-to-nifti
FROM python:3.12-slim
RUN pip install --no-cache-dir "numpy<2.0.0" SimpleITK==2.4.1 pydicom==2.4.4
WORKDIR /app
COPY dicom_to_nifti.py suv.py /app/
