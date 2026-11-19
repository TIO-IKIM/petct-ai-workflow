"""DICOM series folder -> NIfTI (CT in HU, PET as SUVbw), any vendor."""
import argparse
import ast
import os
import sys
from collections import defaultdict

import numpy as np
import pydicom
import SimpleITK as sitk

from suv import SUVError, suv_factors

# geometry tags of a single-frame image (non-image objects like SR or RTSTRUCT lack them)
IMAGE_TAGS = ('ImagePositionPatient', 'ImageOrientationPatient', 'PixelSpacing')


def log(message):
    print(message, flush=True)


def read_headers(folder):
    """All image DICOM headers in folder (recursive), grouped by SeriesInstanceUID."""
    series = defaultdict(list)
    skipped = 0
    for root, _, files in os.walk(folder):
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
            except Exception:
                skipped += 1
                continue
            # pixel data is not loaded here: recognise images by Rows and geometry (or multi-frame)
            single_frame_image = all(hasattr(ds, t) for t in IMAGE_TAGS)
            multi_frame_image = int(getattr(ds, 'NumberOfFrames', 1) or 1) > 1
            if not hasattr(ds, 'Rows') or not (single_frame_image or multi_frame_image):
                skipped += 1
                continue
            ds.filename = path
            series[str(getattr(ds, 'SeriesInstanceUID', 'unknown'))].append(ds)
    if skipped:
        log(f'Skipped {skipped} files that are not DICOM images')
    return series


def choose_series(series, series_uid):
    if not series:
        raise SystemExit('ERROR: no DICOM images found')
    if series_uid:
        if series_uid not in series:
            raise SystemExit(f'ERROR: series {series_uid} not found; available: {list(series)}')
        return series[series_uid]
    if len(series) > 1:
        sizes = {uid: len(h) for uid, h in series.items()}
        log(f'WARNING: {len(series)} series in folder {sizes}; using the one with the most files')
    return max(series.values(), key=len)


def slice_geometry(headers):
    """Sort slices along the normal; return headers, positions along the normal and geometry."""
    iop = np.array([float(x) for x in headers[0].ImageOrientationPatient])
    row, col = iop[:3], iop[3:]
    normal = np.cross(row, col)
    for ds in headers[1:]:
        other = np.array([float(x) for x in ds.ImageOrientationPatient])
        if not np.allclose(other, iop, atol=1e-3):
            raise SystemExit('ERROR: slices with different ImageOrientationPatient in one series')

    positions = np.array([[float(x) for x in ds.ImagePositionPatient] for ds in headers])
    distance = positions @ normal
    order = np.argsort(distance, kind='stable')
    headers = [headers[i] for i in order]
    positions, distance = positions[order], distance[order]

    if len(headers) > 1:
        steps = np.diff(distance)
        if np.any(np.abs(steps) < 1e-3):
            n = int(np.sum(np.abs(steps) < 1e-3))
            raise SystemExit(f'ERROR: {n} slices share a position (dynamic, gated or several '
                             'acquisitions in one series); split the series first')
        axis = positions[-1] - positions[0]
        axis /= np.linalg.norm(axis)
        tilt = np.degrees(np.arccos(min(1.0, abs(float(axis @ normal)))))
        if tilt > 0.5:
            raise SystemExit(f'ERROR: gantry tilt of {tilt:.1f} degrees is not supported')
    return headers, positions, distance, row, col, normal


def read_pixels(headers):
    """Pixels in the given slice order, rescaled per slice, as float32 (slices, rows, cols)."""
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames([ds.filename for ds in headers])
    reader.SetOutputPixelType(sitk.sitkFloat32)
    return sitk.GetArrayFromImage(reader.Execute())


def uniform_slices(array, distance):
    """Resample along the slice axis to the median spacing if the spacing is not uniform."""
    if len(distance) < 2:
        return array, distance, 1.0
    steps = np.diff(distance)
    spacing = float(np.median(steps))
    if np.max(np.abs(steps - spacing)) <= 0.01 * spacing + 1e-3:
        return array, distance, spacing
    log(f'Unequal slice spacing ({steps.min():.2f}-{steps.max():.2f} mm); '
        f'resampling to {spacing:.2f} mm')
    count = int(np.floor((distance[-1] - distance[0]) / spacing + 1e-3)) + 1
    target = distance[0] + spacing * np.arange(count)
    upper = np.clip(np.searchsorted(distance, target, side='right'), 1, len(distance) - 1)
    lower = upper - 1
    weight = ((target - distance[lower]) / (distance[upper] - distance[lower]))[:, None, None]
    weight = np.clip(weight, 0, 1).astype(np.float32)
    return (1 - weight) * array[lower] + weight * array[upper], target, spacing


def to_image(array, origin, row, col, normal, pixel_spacing, slice_spacing):
    image = sitk.GetImageFromArray(array)
    # DICOM PixelSpacing is (row spacing, column spacing) = (y, x)
    image.SetSpacing((float(pixel_spacing[1]), float(pixel_spacing[0]), float(slice_spacing)))
    image.SetOrigin(tuple(float(x) for x in origin))
    image.SetDirection(tuple(np.column_stack([row, col, normal]).flatten().tolist()))
    return image


def read_multiframe(ds):
    log('Single multi-frame file: reading geometry from the file')
    return sitk.Cast(sitk.ReadImage(ds.filename), sitk.sitkFloat32)


def resize(image, out_size):
    size = ast.literal_eval(out_size)
    spacing = [image.GetSpacing()[i] * image.GetSize()[i] / size[i] for i in range(3)]
    log(f'Resizing to {tuple(size)}')
    return sitk.Resample(image, [int(x) for x in size], sitk.Transform(), sitk.sitkLinear,
                         image.GetOrigin(), spacing, image.GetDirection(), 0, image.GetPixelID())


def convert(folder, output, pet_mode='suv', series_uid=None, allow_estimates=False, out_size=None):
    headers = choose_series(read_headers(folder), series_uid)
    first = headers[0]
    modality = str(getattr(first, 'Modality', 'OT')).upper()
    log(f'{modality} series {getattr(first, "SeriesDescription", "")!r}: {len(headers)} files, '
        f'{getattr(first, "Manufacturer", "unknown vendor")}')

    if len(headers) == 1 and int(getattr(first, 'NumberOfFrames', 1) or 1) > 1:
        image = read_multiframe(first)
        if modality == 'PT' and pet_mode == 'suv':
            factor = suv_factors(headers, allow_estimates, log)[0]
            image = image * factor
    else:
        headers, positions, distance, row, col, normal = slice_geometry(headers)
        array = read_pixels(headers)
        if modality == 'PT' and pet_mode == 'suv':
            factors = np.asarray(suv_factors(headers, allow_estimates, log), dtype=np.float32)
            log(f'SUVbw factor {factors[0]:.6g}' + ('' if np.allclose(factors, factors[0]) else
                f' ({factors.min():.6g}-{factors.max():.6g} per slice)'))
            array = array * factors[:, None, None]
        array, _, slice_spacing = uniform_slices(array, distance)
        # slices are sorted along the normal, so the normal is the slice axis and the first
        # sorted position the origin, whatever the file order or InstanceNumber
        image = to_image(array, positions[0], row, col, normal, first.PixelSpacing, slice_spacing)

    if modality == 'CT':
        array = sitk.GetArrayFromImage(image)
        if np.allclose(array, np.round(array)) and array.min() >= -32768 and array.max() <= 32767:
            image = sitk.Cast(image, sitk.sitkInt16)

    if out_size:
        image = resize(image, out_size)

    end = image.TransformIndexToPhysicalPoint([s - 1 for s in image.GetSize()])
    log(f'Output {image.GetSize()} spacing {tuple(round(s, 3) for s in image.GetSpacing())} '
        f'from {tuple(round(x, 1) for x in image.GetOrigin())} to {tuple(round(x, 1) for x in end)}')
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    sitk.WriteImage(image, output)
    log(f'Written {output}')


def main():
    parser = argparse.ArgumentParser(description='Convert a DICOM series to NIfTI (PET as SUV).')
    parser.add_argument('-i', '--input', required=True, help='DICOM folder')
    parser.add_argument('-o', '--output', required=True, help='output .nii or .nii.gz file')
    parser.add_argument('-s', '--output_size', help='resize to this size, e.g. "(128, 128, 256)"')
    parser.add_argument('--pet', choices=['suv', 'raw'], default='suv',
                        help='PET values: SUVbw (default) or the rescaled DICOM values')
    parser.add_argument('--series-uid', help='SeriesInstanceUID to convert if the folder has several')
    parser.add_argument('--allow-estimates', action='store_true',
                        help='estimate missing weight (80 kg), dose (450 MBq) or injection time '
                             '(75 min uptake) instead of failing')
    args = parser.parse_args()
    try:
        convert(args.input, args.output, args.pet, args.series_uid, args.allow_estimates, args.output_size)
    except SUVError as error:
        sys.exit(f'ERROR: cannot compute SUV: {error} (use --pet raw to write the raw values)')


if __name__ == '__main__':
    main()
