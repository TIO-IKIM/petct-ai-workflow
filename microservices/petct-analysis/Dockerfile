# petct-analysis v4: v1 with corrected SUV calculation (utils.py) and the InsightMIP style of
# the paper (calculation.py).
#  - decay time from SeriesTime (QIBA), not an arbitrary slice's AcquisitionTime
#  - DICOM times with and without fractional seconds
#  - Units: BQML, GML and Philips CNTS (7053,1000 / 7053,1009); other units raise an error
#  - PET read as float32, so per-slice RescaleSlope is not clipped to int16
#  - InsightMIP in colour: yellow lesion contours and anatomy tags, black arrows with SUVmax
#    of the three hottest lesions (3D clusters, no longer repeated values of one region)
#  - reference organs eroded as in the paper: liver 2 voxels, aorta 1 voxel in every direction
#  - no scan date inside the MIP: InsightMIP shows it in the header of each time point
# Build: docker build --platform linux/amd64 -t negarshahamiri/petct-analysis:v4 microservices/petct-analysis
FROM negarshahamiri/petct-analysis:v1
COPY utils.py calculation.py /app/
