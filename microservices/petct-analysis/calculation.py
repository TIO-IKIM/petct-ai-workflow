import numpy as np
import matplotlib.pyplot as plt
import SimpleITK as sitk
import os
from map_to_binary import class_map
import cv2
from scipy.ndimage import label
from scipy.ndimage import binary_erosion
from scipy import ndimage
from io import BytesIO
from pydicom.dataset import Dataset
from PIL import Image
from datetime import datetime
import pydicom
import SimpleITK as sitk
import matplotlib.patches as mpatches

def anatomy_tag(ct_label, pet_label):
    """
    Relabel the PET labels based on whether the region belongs to 'lung', 'bone', or 'liver'.

    Parameters:
    - ct_labels: Path to the CT label volume (input file)
    - pet_labels_path: Path to the PET label volume (input file)
    - returns value with lung:200, liver:201, bone:202
    """
    pet_label_anatomy = pet_label
    for value, key in class_map["lung"].items():
        pet_label_anatomy[(ct_label == value) * (pet_label>0)] = 200

    pet_label_anatomy[(ct_label == 5) * (pet_label>0)] = 201

    for value, key in class_map["bone"].items():
        pet_label_anatomy[(ct_label == value) * (pet_label>0)] = 202
    

    return pet_label_anatomy

def overlay_label_on_image(image, label, alpha=0.5):
    RGBforLabel = {1: (0, 0, 1), 2: (0, 1, 1)}
    contours, _ = cv2.findContours(label.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    for c in contours:
        mask = np.zeros_like(image, dtype=np.float64)
        cv2.drawContours(mask, [c], -1, 1, -1)
        colour = RGBforLabel.get(1)
        for i in range(3):
            image[:, :, i] = np.where(mask[:, :, 0] > 0, 
                                      (1 - alpha) * image[:, :, i] + alpha * colour[i], 
                                      image[:, :, i])
    return image

def label_mip(label, axis):
    label1 = np.max(label, axis=axis)
    label1 = cv2.flip(label1, 0)
    return label1


def prepare_view(pet_volume, pet_label_volume, thresh, axis):
    pet_mip = np.max(pet_volume, axis=axis)
    pet_mip = np.clip(pet_mip, 0, thresh)
    pet_mip = np.max(pet_mip) - pet_mip
    pet_mip = np.flip(pet_mip, 0)
    return pet_mip

def find_ct_border(array):
    """
    Crop a 3D NumPy array to the bounding box of the largest cluster where the value is -1024.
    
    Parameters:
        array (np.ndarray): Input 3D NumPy array.
        
    Returns:
        np.ndarray: Cropped 3D array containing the largest cluster.
    """
    # Create a mask for the values equal to -1024 in case ct volume is padded
    mask = (array == -1024) 
    if np.any(mask):
        labeled_array, num_features = label(mask)

        cluster_sizes = [(i, np.sum(labeled_array == i)) for i in range(1, num_features + 1)]

        largest_cluster_label = max(cluster_sizes, key=lambda x: x[1])[0]
        
        largest_cluster_coords = np.argwhere(labeled_array != largest_cluster_label)
        
        min_coords = largest_cluster_coords.min(axis=0)
        max_coords = largest_cluster_coords.max(axis=0)

        return min_coords, max_coords
    else:
        return []


def is_too_close(new_pos, positions, threshold=10):
    """Check if new_pos is too close to any position in positions."""
    for pos in positions:
        if abs(new_pos[0] - pos[0]) <= threshold and abs(new_pos[1] - pos[1]) <= threshold:
            return True
    return False


def draw_contours_with_max_intensity(pet_anatomy_label_mip, label_names, tag, axes, annotations):
    """
    Draw the lesion contours (yellow) with anatomy tags, and arrows with SUVmax for the
    annotated lesions (InsightMIP style of the paper).

    Parameters:
    - pet_anatomy_label_mip: MIP of the lesion labels with anatomy tags (200 lung, 201 liver, 202 bone)
    - label_names: tag text per label
    - tag: 'coronal' or 'sagittal' (arrows point right or left)
    - axes: Matplotlib axes to plot on
    - annotations: [(suv_max, (x, y)), ...] for the three hottest lesions, see top3_suv_clusters
    """
    color = 'yellow'
    label_box = dict(boxstyle="square,pad=0.15", facecolor="dimgray", edgecolor="none", alpha=0.85)
    height, width = axes.get_images()[0].get_array().shape
    for label in np.unique(pet_anatomy_label_mip)[1:]:
        mask = np.uint8(pet_anatomy_label_mip == label)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if cv2.moments(contour)["m00"] == 0:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            polygon = mpatches.Polygon(contour[:, 0, :], closed=True, edgecolor=color, facecolor="none", linewidth=1.2)
            axes.add_patch(polygon)
            if label in label_names and label != 1:
                # organ tag (Lu, Li, B)
                axes.text(x + w, y, label_names[label], color=color, fontsize=10, fontweight='bold',
                          ha='left', va='bottom', bbox=label_box, zorder=5)

    # SUVmax labels on the left, with a line to each lesion
    min_gap = 0.05 * height
    previous_y = -np.inf
    for suv_max, (cX, cY) in sorted(annotations, key=lambda a: a[1][1]):
        label_y = max(cY, previous_y + min_gap)
        previous_y = label_y
        axes.plot(cX, cY, marker='o', markersize=4, markerfacecolor='none', markeredgecolor=color,
                  markeredgewidth=1, zorder=6)
        axes.annotate(rf"SUV$_{{max}}$={suv_max:.1f}", xy=(cX, cY), xytext=(0.02 * width, label_y),
                      color=color, fontsize=10, fontweight='bold', ha='left', va='center', zorder=6,
                      bbox=label_box,
                      arrowprops=dict(arrowstyle="-", color=color, lw=1, relpos=(1, 0.5), shrinkB=3))


def top3_suv_clusters(pet_label_vol, suv_vol):
    """The three lesions (3D connected components) with the highest SUVmax: [(suv_max, centroid)]."""
    label_map, n_label = ndimage.label(pet_label_vol > 0)
    top = []
    for label_id in range(1, n_label + 1):
        comp_mask = label_map == label_id
        top.append((float(suv_vol[comp_mask].max()), tuple(np.argwhere(comp_mask).mean(axis=0).astype(int))))
    return sorted(top, key=lambda x: x[0], reverse=True)[:3]


def save_figure_as_dicomsec(fig, output_path, dicom_dir):
    """
    Save a Matplotlib figure as a DICOM Secondary Capture (DICOMSEC) file with a study date and accession number.

    Parameters:
        fig (matplotlib.figure.Figure): The figure to save.
        output_path (str): Path to save the DICOM file.
        accession_number (str): Accession number for the DICOM metadata.
    """
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
    buf.seek(0)
    img = Image.open(buf).convert("RGB")  # colour: yellow contours
    array = np.array(img)

    dicom_file = next(
        (
            os.path.join(dicom_dir, f)
            for f in os.listdir(dicom_dir)
            if f.lower().endswith(".dcm")
        ),
        None,
    )
    if dicom_file is None:
        raise FileNotFoundError("No DICOM file found in directory.")

    ref_ds = pydicom.dcmread(dicom_file)


    ds = ref_ds.copy()

    ds.Modality = "SC"
    ds.SeriesNumber = 301 # arbitrary
    ds.Rows, ds.Columns = array.shape[:2]
    ds.PixelData = array.astype(np.uint8).tobytes()
    ds.InstanceNumber = 1
    ds.PhotometricInterpretation = "RGB"
    ds.SamplesPerPixel = 3
    ds.PlanarConfiguration = 0
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = array.astype(np.uint8).tobytes()

    pixel_spacing = ref_ds.get("PixelSpacing", [1.0, 1.0])
    slice_thickness = ref_ds.get("SliceThickness", 1.0)
    ds.PixelSpacing = [slice_thickness,pixel_spacing[0]]  # [X spacing, Y spacing]
    print(pixel_spacing, slice_thickness)

    pydicom.dcmwrite(output_path, ds)
    print(f"Secondary Capture DICOM saved to {output_path}")


def save_mip_with_overlay(
    pet_volume_path,
    ct_volume_path,
    ct_label_path,
    pet_label_path,
    output_path,
    suv_path,
    dicom_pet_path,
    date,
):
    pet_volume = sitk.GetArrayFromImage(sitk.ReadImage(pet_volume_path))
    ct_volume = sitk.GetArrayFromImage(sitk.ReadImage(ct_volume_path))
    pet_label_volume = sitk.GetArrayFromImage(sitk.ReadImage(pet_label_path))
    ct_label_volume = sitk.GetArrayFromImage(sitk.ReadImage(ct_label_path))
    suv_image = sitk.ReadImage(suv_path)
    suv_map = sitk.GetArrayFromImage(suv_image)
    # slices are thicker than the pixels, so correct the aspect ratio
    spacing_x, spacing_y, spacing_z = suv_image.GetSpacing()

    masked_values = suv_map[(ct_volume == -1024) & (pet_label_volume == 0)]

    if masked_values.size > 0:
        background = np.mean(masked_values)
    else:
        background = 0


    coords = find_ct_border(ct_volume)

    if coords:
        min_coords, max_coords = coords[0], coords[1]
        pet_volume = pet_volume[min_coords[0]:max_coords[0] + 1,
                                min_coords[1]:max_coords[1] + 1,
                                min_coords[2]:max_coords[2] + 1]

        ct_volume = ct_volume[min_coords[0]:max_coords[0] + 1,
                                min_coords[1]:max_coords[1] + 1,
                                min_coords[2]:max_coords[2] + 1]

        pet_label_volume = pet_label_volume[min_coords[0]:max_coords[0] + 1,
                                            min_coords[1]:max_coords[1] + 1,
                                            min_coords[2]:max_coords[2] + 1]

        ct_label_volume = ct_label_volume[min_coords[0]:max_coords[0] + 1,
                                min_coords[1]:max_coords[1] + 1,
                                min_coords[2]:max_coords[2] + 1]

        suv_map = suv_map[min_coords[0]:max_coords[0] + 1,
                            min_coords[1]:max_coords[1] + 1,
                            min_coords[2]:max_coords[2] + 1]

    threshold = -1000
    pet_label_volume[ct_volume < threshold] = 0

    pet_label_anatomy = anatomy_tag(ct_label_volume, pet_label_volume)
    

    aorta_mask = (ct_label_volume == 52) & (pet_label_volume == 0)
    liver_mask = (ct_label_volume == 5) & (pet_label_volume == 0)

    # as in the paper: liver eroded by 2 voxels, aorta (blood pool) by 1 voxel in every direction
    # (odd, centred structuring elements; even sizes erode unevenly)
    aorta_eroded = binary_erosion(aorta_mask, structure=np.ones((3, 3, 3)))
    liver_eroded = binary_erosion(liver_mask, structure=np.ones((5, 5, 5)))

    aorta_max = np.max(suv_map[aorta_eroded == 1])
    liver_max = np.max(suv_map[liver_eroded == 1])


    pet_label_volume1 = np.zeros_like(pet_label_volume)
    pet_label_volume1[(suv_map > 0) & (suv_map <= background)] = 0
    pet_label_volume1[(suv_map > background) & (suv_map <= aorta_max)] = 2
    pet_label_volume1[(suv_map > aorta_max) & (suv_map <= liver_max)] = 3
    pet_label_volume1[(suv_map > liver_max) & (suv_map <= 2.5 * liver_max)] = 4
    pet_label_volume1[(suv_map > (2.5 * liver_max))] = 5
    pet_label_volume1[pet_label_volume == 0] = 0

    # three hottest lesions as 3D clusters, projected to the coronal and sagittal MIP
    top3 = top3_suv_clusters(pet_label_volume1, suv_map)
    depth = pet_label_volume1.shape[0]
    annot_cor = [(suv, (coord[2], depth - 1 - coord[0])) for suv, coord in top3]
    annot_sag = [(suv, (coord[1], depth - 1 - coord[0])) for suv, coord in top3]

    thresh = 1.5*liver_max 
    coronal_pet_mip = prepare_view(suv_map, pet_label_volume1, thresh, axis=1)
    sagittal_pet_mip = prepare_view(suv_map, pet_label_volume1, thresh, axis=2)

    fig, axes = plt.subplots(1, 2, facecolor='white', edgecolor='black', figsize=(10, 10))

    axes[0].imshow(coronal_pet_mip, 'gray', interpolation='bilinear', aspect=spacing_z / spacing_x)
    axes[0].axis('off')
    # coronal labels point right, into the sagittal panel: draw the coronal panel on top
    axes[0].set_zorder(2)

    label_names = {1:'others', 200: 'Lu', 201: 'Li', 202: 'B'}
    pet_anatomy_label_mip_coronal = label_mip(pet_label_anatomy, 1)
    draw_contours_with_max_intensity(pet_anatomy_label_mip_coronal, label_names, 'coronal', axes[0], annot_cor)



    axes[1].imshow(sagittal_pet_mip, 'gray', interpolation='bilinear', aspect=spacing_z / spacing_y)
    axes[1].axis('off')

    pet_anatomy_label_mip_sagittal = label_mip(pet_label_anatomy, 2)
    draw_contours_with_max_intensity(pet_anatomy_label_mip_sagittal, label_names, 'sagittal', axes[1], annot_sag)

    annotation_text = f"blood pool: {aorta_max:.1f}\n liver: {liver_max:.1f}"
    axes[0].text(0.01, 0.99, annotation_text, transform=axes[0].transAxes,
                fontsize=10, color='black', verticalalignment='top',
                bbox=dict(facecolor='white', alpha=0.7, edgecolor='black'))

    plt.tight_layout(pad=0.0)
    plt.margins(0,0)
    fig.savefig(os.path.join(output_path, "result.png"), format="png", dpi=300, bbox_inches="tight")
    save_figure_as_dicomsec(
        fig, os.path.join(output_path, "result.dcm"), dicom_pet_path
    )

