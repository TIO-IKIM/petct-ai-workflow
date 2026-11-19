#!/usr/bin/env python3
"""
Created on Wed Mar 24 22:48:15 2021

"""

import os
import traceback
import datetime
import numpy as np
import pydicom
import SimpleITK as sitk
from map_to_binary import class_map
from pydicom_seg.template import from_dcmqi_metainfo
from pydicom_seg import MultiClassWriter


def sort_by_instance_number(dicom_file_list):
    data = []
    for row in dicom_file_list:        
        f=pydicom.dcmread(row)
        data.append({'f':row,'n':f.InstanceNumber})
    data=sorted(data,key=lambda x: x['n'])
    return [x['f'] for x in data]

def get_dicom_file_list(dicom_directory):
    image_file_list = [os.path.join(dicom_directory,x) for x in os.listdir(dicom_directory) if x.endswith('.dcm')]
    sorted_list = sort_by_instance_number(image_file_list)
    return sorted_list


def sitk_read(dicom_directory):
    sitkreader = sitk.ImageSeriesReader()
	# check if there is a DICOM series in the dicom_directory
    series_IDs = sitkreader.GetGDCMSeriesIDs(dicom_directory)
    try:
        series_file_names = sitkreader.GetGDCMSeriesFileNames(dicom_directory, series_IDs[0])
        sitkreader.SetFileNames(series_file_names)
        # Float output: the pixel type is otherwise chosen from the first slice only, and when
        # its RescaleSlope is 1 the volume is int16 and slices with a larger slope are clipped
        # at 32767 (e.g. Philips PET, where RescaleSlope varies per slice).
        sitkreader.SetOutputPixelType(sitk.sitkFloat32)
        sitk_image = sitkreader.Execute()
        array = sitk.GetArrayFromImage(sitk_image)
        spacing = sitk_image.GetSpacing()
        origin = sitk_image.GetOrigin()
        direction = sitk_image.GetDirection()   


        return array, spacing, origin, direction
    
    except:
        if not series_IDs:
        	print("ERROR: given directory \""+dicom_directory+"\" does not contain a DICOM series.")
        else:
            print("UNKNOWN ERROR while scanning directory \""+dicom_directory+"\", skipping...")


def sitk_read_to_size(dicom_directory, sitk_img_with_target_size):
    sitkreader = sitk.ImageSeriesReader()
	# check if there is a DICOM series in the dicom_directory
    series_IDs = sitkreader.GetGDCMSeriesIDs(dicom_directory)

    series_file_names = sitkreader.GetGDCMSeriesFileNames(dicom_directory, series_IDs[0])
    sitkreader.SetFileNames(series_file_names)
    sitk_image = sitkreader.Execute()

    resample_image = sitk.Resample(sitk_image, sitk_img_with_target_size, sitk.Transform(), sitk.sitkNearestNeighbor, 0, sitk_image.GetPixelID())

    array = sitk.GetArrayFromImage(resample_image)    
    spacing = resample_image.GetSpacing()
    origin = resample_image.GetOrigin()
    direction = resample_image.GetDirection()   
   
    return array, spacing, origin, direction



def save_nifti(array, spacing, origin, direction, save_dir, filename):

    image = sitk.GetImageFromArray(array)

    image.SetSpacing(spacing)
    image.SetOrigin(origin)
    image.SetDirection(direction)

    os.makedirs(save_dir, exist_ok=True)

    sitk.WriteImage(image, os.path.join(save_dir, filename))
    print(f'...{filename} written to {save_dir}')

def write_nifti(img, save_dir, filename = 'output.nii.gz'):
    sitk.WriteImage(img, os.path.join(save_dir, filename))
    print(f'...{filename} written to {save_dir}')

def npy2sitk(array, spacing, origin, direction):
    img = sitk.GetImageFromArray(array)
    img.SetSpacing(spacing)
    img.SetOrigin(origin)
    img.SetDirection(direction)
    return img


def sitk2npy(sitk_image):
    array = sitk.GetArrayFromImage(sitk_image)
    spacing = sitk_image.GetSpacing()
    origin = sitk_image.GetOrigin()
    direction = sitk_image.GetDirection()
    return array,spacing,origin,direction

def resample_to_thickness(image, thickness=5):
    input_size = image.GetSize()
    input_spacing = image.GetSpacing()
    output_spacing = (input_spacing[0], input_spacing[1], thickness)
    output_direction = image.GetDirection()
    output_origin = image.GetOrigin()
    output_size = [int(np.round(input_size[i] * input_spacing[i] / output_spacing[i])) for i in range(3)]
    image = sitk.Resample(image, output_size, sitk.Transform(), sitk.sitkLinear, output_origin, output_spacing, output_direction)
    return image
'''

Body-weight SUV (SUVbw), following QIBA and Cornerstone's calculate-suv.
SUV helpers (_parse_dicom_time, _ge_post_injection_time, get_scan_time) are taken from
petct-automated-workflow/src/microservices/dicom-to-nifti/utils.py.
https://qibawiki.rsna.org/index.php/Standardized_Uptake_Value_(SUV)
https://github.com/cornerstonejs/calculate-suv

'''

def _parse_dicom_time(value):
    """DICOM TM ('HHMMSS', 'HHMMSS.ffffff', 'HHMM') -> datetime (date part ignored)."""
    value = str(value).strip()
    for fmt in ('%H%M%S.%f', '%H%M%S', '%H%M'):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f'Unrecognized DICOM time: {value!r}')


def _ge_post_injection_time(ds):
    """GE private tag (0009,100d) 'YYYYMMDDHHMMSS': time the scan started, if present."""
    element = ds.get((0x0009, 0x100D), None)
    if element is None or not element.value:
        return None
    value = element.value.decode(errors='ignore') if isinstance(element.value, bytes) else str(element.value)
    value = value.strip()
    return _parse_dicom_time(value[8:14]) if len(value) >= 14 else None


def get_scan_time(image_file_list, first_ds):
    """
    Scan reference time for decay correction (QIBA, same as Cornerstone's calculate-suv):
    SeriesTime if it is not after the earliest AcquisitionTime of the series; otherwise
    (e.g. GE, where SeriesTime is a later timestamp) the GE post-injection time, else the
    earliest AcquisitionTime. Using one arbitrary slice's AcquisitionTime is wrong for
    multi-bed scans: later bed positions have later times.

    image_file_list is sorted by InstanceNumber (= slice position), and beds are acquired
    in order along the body, so the earliest bed is at one end: first or last slice.
    """
    acquisition_times = []
    for path in {image_file_list[0], image_file_list[-1]}:
        ds = pydicom.dcmread(path, stop_before_pixels=True)
        if getattr(ds, 'AcquisitionTime', None):
            acquisition_times.append(_parse_dicom_time(ds.AcquisitionTime))
    earliest_acquisition = min(acquisition_times) if acquisition_times else None

    series_time = _parse_dicom_time(first_ds.SeriesTime) if getattr(first_ds, 'SeriesTime', None) else None

    if series_time and (earliest_acquisition is None or series_time <= earliest_acquisition):
        return series_time
    ge_time = _ge_post_injection_time(first_ds)
    if ge_time:
        return ge_time
    if earliest_acquisition:
        return earliest_acquisition
    raise ValueError('No SeriesTime or AcquisitionTime for decay correction')


def _private_float(ds, tag):
    element = ds.get(tag, None)
    if element is None or element.value in (None, ''):
        return None
    value = element.value.decode(errors='ignore') if isinstance(element.value, bytes) else element.value
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _bqml_suv_factor(image_file_list, f):
    """weight / decay-corrected dose, for images in Bq/ml."""
    try:
        weight_grams = float(f.PatientWeight)*1000
    except:
        traceback.print_exc()
        weight_grams = 80000
        print ('weight estimated as 80 kg')
    try:
        if str(getattr(f, 'DecayCorrection', '')).upper() == 'ADMIN':
            # Image already decay-corrected to injection time: no dose decay correction.
            decay = 1.0
        else:
            # Scan reference time (series start), not an arbitrary slice's AcquisitionTime
            scantime = get_scan_time(image_file_list, f)
            injection_time = _parse_dicom_time(f.RadiopharmaceuticalInformationSequence[0].RadiopharmaceuticalStartTime)
            half_life = float(f.RadiopharmaceuticalInformationSequence[0].RadionuclideHalfLife)

            decay = np.exp(-np.log(2)*((scantime-injection_time).seconds)/half_life)
            print(f'uptake time {(scantime-injection_time).seconds/60:.1f} min, decay factor {decay:.4f}')

    except:
        traceback.print_exc()
        decay = np.exp(-np.log(2)*(1.25*3600)/6588) # 60 min waiting time, 15 min preparation (in seconds)
        print ('could not fetch data for decay calculation, thus estimating with 75 min scan-delay')

    try:
        injected_dose = float(f.RadiopharmaceuticalInformationSequence[0].RadionuclideTotalDose)

        injected_dose_decay = injected_dose*decay # in Bq
    except:
        traceback.print_exc()
        injected_dose_decay = 450000000 * decay # 450 MBq
        print ('could not fetch injected dose, thus estimating with 450 MBq')

    return weight_grams/injected_dose_decay


def get_suv_factor(image_file_list, f):
    """
    Factor that turns the (rescaled) pixel values into SUVbw, depending on Units (0054,1001):
      BQML      -> weight / decay-corrected dose
      GML       -> already SUVbw, factor 1
      CNTS      -> Philips: SUV Scale Factor (7053,1000), else
                   Activity Concentration Scale Factor (7053,1009) to Bq/ml, then as BQML
    Other units raise an error instead of producing wrong SUVs.
    """
    units = str(getattr(f, 'Units', 'BQML')).strip().upper() or 'BQML'
    if units == 'BQML':
        return _bqml_suv_factor(image_file_list, f)
    if units == 'GML':
        print('Units GML: pixel values are already SUVbw')
        return 1.0
    if units == 'CNTS':
        suv_scale = _private_float(f, (0x7053, 0x1000))
        if suv_scale:
            print(f'Units CNTS: using Philips SUV Scale Factor (7053,1000) = {suv_scale}')
            return suv_scale
        activity_scale = _private_float(f, (0x7053, 0x1009))
        if activity_scale:
            print(f'Units CNTS: using Philips Activity Concentration Scale Factor (7053,1009) = {activity_scale}')
            return activity_scale * _bqml_suv_factor(image_file_list, f)
        raise ValueError('PET Units are CNTS but the Philips scale factors (7053,1000)/(7053,1009) are missing')
    raise ValueError(f'Unsupported PET Units {units!r}: cannot compute SUV')


def suv_read(dicom_directory):
    pet_array,spacing,origin,direction = sitk_read(dicom_directory)

    image_file_list = get_dicom_file_list(dicom_directory)

    f=pydicom.dcmread(image_file_list[0])

    SUVbwScaleFactor = get_suv_factor(image_file_list, f)
    print(f'SUVbw scale factor {SUVbwScaleFactor:.6g}')
    suv_array = pet_array * SUVbwScaleFactor
    return suv_array,spacing,origin,direction 


def combine_labels(segmentation_dir):
    """
    Combine separate label NIfTI files into a single multi-label NIfTI file.

    Args:
        segmentation_dir (str): Directory containing individual label NIfTI files.
        output_file (str): Path to save the combined NIfTI file.
    """
    combined_image = None
    for label_id, label_name in class_map["total"].items():
        label_id = int(label_id)
        label_file = os.path.join(segmentation_dir, f"{label_name}.nii.gz")
        if not os.path.exists(label_file):
            continue

        label_image = sitk.ReadImage(label_file)
        label_array = sitk.GetArrayFromImage(label_image)

        if combined_image is None:
            combined_image = np.zeros_like(label_array, dtype=np.uint8)

        combined_image[label_array > 0] = label_id

    return combined_image

def write_dicom_seg_from_nifti(
    nifti_seg_path: str,
    dicom_dir: str,
    metainfo_json_path: str,
    output_dicom_seg_path: str,
):
    """
    Convert a one-class NIfTI segmentation file to DICOM-SEG format.

    Parameters:
    - nifti_seg_path: path to input NIfTI (.nii/.nii.gz) file
    - dicom_dir: directory containing DICOM files of original study
    - metainfo_json_path: path to DCMQI metainfo.json template
    - output_dicom_seg_path: output path for DICOM-SEG (.dcm)
    """
    first_dicom_file = next(
        (
            os.path.join(dicom_dir, f)
            for f in os.listdir(dicom_dir)
            if f.lower().endswith(".dcm")
        ),
        None,
    )
    if not first_dicom_file:
        raise FileNotFoundError("No DICOM (.dcm) files found in specified directory.")

    ds = pydicom.dcmread(first_dicom_file)
    series_instance_uid = ds.SeriesInstanceUID

    segmentation = sitk.ReadImage(nifti_seg_path)
    segmentation = sitk.Flip(segmentation, [False, False, True])

    reader = sitk.ImageSeriesReader()
    dicom_filenames = reader.GetGDCMSeriesFileNames(dicom_dir, series_instance_uid)
    reader.SetFileNames(dicom_filenames)
    reference_image = reader.Execute()

    segmentation.CopyInformation(reference_image)

    template = from_dcmqi_metainfo(metainfo_json_path)

    writer = MultiClassWriter(
        template=template,
        inplane_cropping=False,
        skip_empty_slices=False,
        skip_missing_segment=False,
    )

    source_images = [
        pydicom.dcmread(fp, stop_before_pixels=True) for fp in dicom_filenames
    ]

    dcm_seg = writer.write(segmentation, source_images)

    dcm_seg.save_as(output_dicom_seg_path)

    print(f"DICOM-SEG saved to: {output_dicom_seg_path}")