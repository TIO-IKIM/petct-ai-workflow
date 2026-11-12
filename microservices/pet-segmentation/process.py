import json
import os
import subprocess

import SimpleITK
import torch

from classify_pet import classify_pet
import nibabel as nib 
import numpy as np
import argparse
import time

class LesionSegmentation:

    def __init__(self,input_path, output_path):
        """
        Write your own input validators here
        Initialize your model etc.
        Assumed folder structure:
        Inputs:
        input_path/petct_0000.nii.gz (CT)
        input_path/petct_0001.nii.gz (PET)
        Outputs:
        output_path/tumor_seg.nii.gz
        """
        # set some paths and parameters
        # according to the specified grand-challenge interfaces
        self.input_path = input_path # contains folder of the ct and pet images
        # according to the specified grand-challenge interfaces
        self.output_path = output_path # path to the folder of labels
        self.pred_path = (
            "/opt/algorithm/prediction"
        )
        self.pet_path = os.path.join(self.input_path, "petct_0001.nii.gz")
        self.seg_file = "tumor_seg.nii.gz"
        self.classifier_path = "/opt/algorithm/tracer_classifier.pt"
        pass

    def convert_mha_to_nii(self, mha_input_path, nii_out_path):  # nnUNet specific
        img = SimpleITK.ReadImage(mha_input_path)
        SimpleITK.WriteImage(img, nii_out_path, True)

    def convert_nii_to_mha(self, nii_input_path, mha_out_path):  # nnUNet specific
        img = SimpleITK.ReadImage(nii_input_path)
        SimpleITK.WriteImage(img, mha_out_path, True)

    def check_gpu(self):
        """
        Check if GPU is available
        """
        print("Checking GPU availability")
        is_available = torch.cuda.is_available()
        print("Available: " + str(is_available))
        print(f"Device count: {torch.cuda.device_count()}")
        if is_available:
            print(f"Current device: {torch.cuda.current_device()}")
            print("Device name: " + torch.cuda.get_device_name(0))
            print(
                "Device memory: "
                + str(torch.cuda.get_device_properties(0).total_memory)
            )


    def write_outputs(self, uuid):
        """
        Write to /output/
        Check https://grand-challenge.org/algorithms/interfaces/
        """
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        self.convert_nii_to_mha(
            os.path.join(self.result_path, self.seg_file),
            os.path.join(self.output_path, uuid + ".mha"),
        )
        print("Output written to: " + os.path.join(self.output_path, uuid + ".mha"))

        
    def predict(self):
        """
        Your algorithm goes here
        """
        
        print("Tracer classification starting now!")
        tracer = classify_pet(self.pet_path, self.classifier_path)
        dataset_nr = 1 if tracer == 'fdg' else 2

        # Run prediction
        print("nnUNet segmentation starting!")
        if dataset_nr == 1:
            cproc = subprocess.run(
                f"nnUNetv2_predict -i {self.input_path} -o {self.pred_path} -d {dataset_nr} -c 3d_fullres -tr nnUNetTrainer_1500epochs_fasterdec -p nnUNetResEncUNetMPlans --disable_tta",
                shell=True,
                check=True,
            )
        else:
            cproc = subprocess.run(
                f"nnUNetv2_predict -i {self.input_path} -o {self.pred_path} -d {dataset_nr} -c 3d_fullres -p nnUNetResEncUNetMPlans --disable_tta",
                shell=True,
                check=True,
            )        
        print(cproc)
        
        print("Prediction finished")

        return tracer

    def postprocess(self,tracer):

        seg = nib.load(os.path.join(self.pred_path, 'petct.nii.gz'))
        seg_arr = seg.get_fdata()
        pet_path = os.path.join(self.input_path,'petct_0001.nii.gz')
        pet = nib.load(pet_path).get_fdata()
        suv_threshold = 1.5 if tracer == 'fdg' else 1
        seg_arr[pet < suv_threshold] = 0
        seg_arr = np.where(seg_arr==1,1,0)
        final_seg = nib.Nifti1Image(seg_arr.astype(np.int16), seg.affine, seg.header)
        nib.save(final_seg, os.path.join(self.output_path, self.seg_file))
            
    def process(self):
        """
        Read inputs from /input, process with your algorithm and write to /output
        """
        # process function will be called once for each test sample
        
        self.check_gpu()
        print("Start processing")
        tracer = self.predict()
        print("Start postprocessing")
        self.postprocess(tracer)


if __name__ == "__main__":
    print("START")
    st = time.time()
    parser = argparse.ArgumentParser(description='Lesion Segmentation using PET-CT images')
    
    # Add input and output path arguments
    parser.add_argument('-i', '--input_path', type=str, required=True, 
                        help='Path to the directory containing input PET-CT images')
    parser.add_argument('-o', '--output_path', type=str, required=True, 
                        help='Path to the directory for saving output segmentation results')
    
    # Parse the arguments
    args = parser.parse_args()

    print("START")
    
    # Run the process method
    LesionSegmentation(input_path=args.input_path, output_path=args.output_path).process()
    ent = time.time()
    print('time: ', ent-st)
