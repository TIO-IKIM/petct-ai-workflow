import SimpleITK as sitk
import argparse

def resize_vol1_to_vol2(vol2_path, vol1_path):
    vol2_img = sitk.ReadImage(vol2_path)
    vol1_img = sitk.ReadImage(vol1_path)
    
    vol2_size = vol2_img.GetSize()
    vol2_spacing = vol2_img.GetSpacing()
    
    resample = sitk.ResampleImageFilter()
    resample.SetOutputSpacing(vol2_spacing)
    resample.SetSize(vol2_size)
    resample.SetInterpolator(sitk.sitkLinear)
    resample.SetOutputDirection(vol2_img.GetDirection())
    resample.SetOutputOrigin(vol2_img.GetOrigin())
    
    resized_vol1_img = resample.Execute(vol1_img)
    sitk.WriteImage(resized_vol1_img, vol1_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resize vol1 to match vol2 dimensions.")
    parser.add_argument('-vol1', '--vol1_path', type=str, help="Path to the vol1 nifti.")
    parser.add_argument('-vol2', '--vol2_path',  type=str, help="Path to the vol2 nifti.")
    args = parser.parse_args()

    resize_vol1_to_vol2(args.vol2_path, args.vol1_path)
    