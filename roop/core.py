#!/usr/bin/env python3

import os
import sys
import shutil
# single thread doubles cuda performance - needs to be set before torch import
if any(arg.startswith('--execution-provider') for arg in sys.argv):
    os.environ['OMP_NUM_THREADS'] = '1'

import warnings
from typing import List
import platform
import signal
import torch
import onnxruntime
import pathlib

from time import time

import roop.globals
import roop.metadata
import roop.utilities as util
import roop.util_ffmpeg as ffmpeg
import roop.ui as ui
from settings import Settings
from roop.face_util import extract_face_images
from roop.ProcessEntry import ProcessEntry
from roop.ProcessMgr import ProcessMgr
from roop.ProcessOptions import ProcessOptions
from roop.capturer import get_video_frame_total

#----------------------------
from hashlib import sha256    
from Crypto.Cipher import AES
from Crypto.Util import Padding
from urllib.request import Request
   
import hashlib
import argparse
import urllib.request
import random
import timeit
import string
print("new hereeeeeeeeeeeee")    
#---------------------------

clip_text = None

call_display_ui = None

process_mgr = None


if 'ROCMExecutionProvider' in roop.globals.execution_providers:
    del torch

warnings.filterwarnings('ignore', category=FutureWarning, module='insightface')
warnings.filterwarnings('ignore', category=UserWarning, module='torchvision')

#------------------------------
def encryption(filenew):
    def filencrypt(pswd, iv, file):
        key = hashlib.sha256(pswd.encode()).digest()

        print (f"IV={iv}")
        

        with open(file, "rb") as f:
            data = f.read()

        #stime = timeit.default_timer()
        
        cipher = AES.new(key, AES.MODE_CBC, iv)
        paddeddata = Padding.pad(data, 16)
        encrypteddata = cipher.encrypt(paddeddata)
        
        with open(file, "wb") as ef:
            ef.write(encrypteddata)

        #time = timeit.default_timer() - stime

        print("file  completeee!\n")
       

    #file = input("\nFile to encrypt : " )
    #pswd = input("Choose a strong password : ")

    def geniv(length):
        str = string.ascii_uppercase + string.digits #+ string.punctuation
        return "".join(random.choice(str) for i in range(length))

    iv = geniv(16)
                
    filencrypt("t@$t!n1", iv.encode(), filenew)

#-----------------------------------------
#-----------------------------

def parse_args() -> None:
    signal.signal(signal.SIGINT, lambda signal_number, frame: destroy())
    roop.globals.headless = False
    # Always enable all processors when using GUI
    if len(sys.argv) > 1:
        print('No CLI args supported - use Settings Tab instead')
    roop.globals.frame_processors = ['face_swapper', 'face_enhancer']


def encode_execution_providers(execution_providers: List[str]) -> List[str]:
    return [execution_provider.replace('ExecutionProvider', '').lower() for execution_provider in execution_providers]


def decode_execution_providers(execution_providers: List[str]) -> List[str]:
    return [provider for provider, encoded_execution_provider in zip(onnxruntime.get_available_providers(), encode_execution_providers(onnxruntime.get_available_providers()))
            if any(execution_provider in encoded_execution_provider for execution_provider in execution_providers)]


def suggest_max_memory() -> int:
    if platform.system().lower() == 'darwin':
        return 4
    return 16


def suggest_execution_providers() -> List[str]:
    return encode_execution_providers(onnxruntime.get_available_providers())


def suggest_execution_threads() -> int:
    if 'DmlExecutionProvider' in roop.globals.execution_providers:
        return 1
    if 'ROCMExecutionProvider' in roop.globals.execution_providers:
        return 1
    return 8


def limit_resources() -> None:
    # limit memory usage
    if roop.globals.max_memory:
        memory = roop.globals.max_memory * 1024 ** 3
        if platform.system().lower() == 'darwin':
            memory = roop.globals.max_memory * 1024 ** 6
        if platform.system().lower() == 'windows':
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetProcessWorkingSetSize(-1, ctypes.c_size_t(memory), ctypes.c_size_t(memory))
        else:
            import resource
            resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))



def release_resources() -> None:
    import gc
    global process_mgr

    if process_mgr is not None:
        process_mgr.release_resources()
        process_mgr = None

    gc.collect()
    if 'CUDAExecutionProvider' in roop.globals.execution_providers and torch.cuda.is_available():
        with torch.cuda.device('cuda'):
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()


def pre_check() -> bool:
    if sys.version_info < (3, 9):
        update_status('Python version is not supported - please upgrade to 3.9 or higher.')
        return False
    
    download_directory_path = util.resolve_relative_path('../models')
    util.conditional_download(download_directory_path, ['https://huggingface.co/countfloyd/deepfake/resolve/main/inswapper_128.onnx'])
    util.conditional_download(download_directory_path, ['https://huggingface.co/countfloyd/deepfake/resolve/main/GFPGANv1.4.onnx'])
    util.conditional_download(download_directory_path, ['https://huggingface.co/countfloyd/deepfake/resolve/main/GPEN-BFR-512.onnx'])
    util.conditional_download(download_directory_path, ['https://github.com/csxmli2016/DMDNet/releases/download/v1/DMDNet.pth'])
    download_directory_path = util.resolve_relative_path('../models/CLIP')
    util.conditional_download(download_directory_path, ['https://huggingface.co/countfloyd/deepfake/resolve/main/rd64-uni-refined.pth'])
    download_directory_path = util.resolve_relative_path('../models/CodeFormer')
    util.conditional_download(download_directory_path, ['https://huggingface.co/countfloyd/deepfake/resolve/main/CodeFormerv0.1.onnx'])

    if not shutil.which('ffmpeg'):
       update_status('ffmpeg is not installed.')
    return True

def set_display_ui(function):
    global call_display_ui

    call_display_ui = function


def update_status(message: str) -> None:
    global call_display_ui

    print(message)
    if call_display_ui is not None:
        call_display_ui(message)




def start() -> None:
    if roop.globals.headless:
        print('Headless mode currently unsupported - starting UI!')
        # faces = extract_face_images(roop.globals.source_path,  (False, 0))
        # roop.globals.INPUT_FACES.append(faces[roop.globals.source_face_index])
        # faces = extract_face_images(roop.globals.target_path,  (False, util.has_image_extension(roop.globals.target_path)))
        # roop.globals.TARGET_FACES.append(faces[roop.globals.target_face_index])
        # if 'face_enhancer' in roop.globals.frame_processors:
        #     roop.globals.selected_enhancer = 'GFPGAN'
       
    batch_process(None, False, None)


def get_processing_plugins(use_clip):
    processors = "faceswap"
    if use_clip:
        processors += ",mask_clip2seg"
    
    if roop.globals.selected_enhancer == 'GFPGAN':
        processors += ",gfpgan"
    elif roop.globals.selected_enhancer == 'Codeformer':
        processors += ",codeformer"
    elif roop.globals.selected_enhancer == 'DMDNet':
        processors += ",dmdnet"
    return processors


def live_swap(frame, swap_mode, use_clip, clip_text, selected_index = 0):
    global process_mgr

    if frame is None:
        return frame

    if process_mgr is None:
        process_mgr = ProcessMgr(None)
    
    options = ProcessOptions(get_processing_plugins(use_clip), roop.globals.distance_threshold, roop.globals.blend_ratio, swap_mode, selected_index, clip_text)
    process_mgr.initialize(roop.globals.INPUT_FACES, roop.globals.TARGET_FACES, options)
    return process_mgr.process_frame(frame)


def preview_mask(frame, clip_text):
    import numpy as np
    global process_mgr
    
    maskimage = np.zeros((frame.shape), np.uint8)
    if process_mgr is None:
        process_mgr = ProcessMgr(None)
    options = ProcessOptions("mask_clip2seg", roop.globals.distance_threshold, roop.globals.blend_ratio, "None", 0, clip_text)
    process_mgr.initialize(roop.globals.INPUT_FACES, roop.globals.TARGET_FACES, options)
    maskprocessor = next((x for x in process_mgr.processors if x.processorname == 'clip2seg'), None)
    return process_mgr.process_mask(maskprocessor, frame, maskimage)
    




def batch_process(files:list[ProcessEntry], use_clip, new_clip_text, use_new_method, progress) -> None:
    global clip_text, process_mgr

    roop.globals.processing = True
    release_resources()
    limit_resources()

    # limit threads for some providers
    max_threads = suggest_execution_threads()
    if max_threads == 1:
        roop.globals.execution_threads = 1

    imagefiles:list[ProcessEntry] = []
    videofiles:list[ProcessEntry] = []
           
    update_status('Sorting videos/images')


    for index, f in enumerate(files):
        fullname = f.filename
        if util.has_image_extension(fullname):
            destination = util.get_destfilename_from_path(fullname, roop.globals.output_path, f'.{roop.globals.CFG.output_image_format}')
            destination = util.replace_template(destination, index=index)
            pathlib.Path(os.path.dirname(destination)).mkdir(parents=True, exist_ok=True)
            f.finalname = destination
            imagefiles.append(f)

        elif util.is_video(fullname) or util.has_extension(fullname, ['gif']):
            destination = util.get_destfilename_from_path(fullname, roop.globals.output_path, f'__temp.{roop.globals.CFG.output_video_format}')
            f.finalname = destination
            videofiles.append(f)


    if process_mgr is None:
        process_mgr = ProcessMgr(progress)
    
    options = ProcessOptions(get_processing_plugins(use_clip), roop.globals.distance_threshold, roop.globals.blend_ratio, roop.globals.face_swap_mode, 0, new_clip_text)
    process_mgr.initialize(roop.globals.INPUT_FACES, roop.globals.TARGET_FACES, options)

    if(len(imagefiles) > 0):
        update_status('Processing image(s)')
        origimages = []
        fakeimages = []
        for f in imagefiles:
            origimages.append(f.filename)
            fakeimages.append(f.finalname)

        process_mgr.run_batch(origimages, fakeimages, roop.globals.execution_threads)
        if os.path.isfile(fakeimages[0]):            
            #print("fakeimages="+pathlib.Path(os.path.dirname(fakeimages[0])))   
            #print("fakeimagess="+fakeimages[0])
            encryption(fakeimages[0])       
            origimages.clear()
            fakeimages.clear()

    if(len(videofiles) > 0):
        for index,v in enumerate(videofiles):
            if not roop.globals.processing:
                end_processing('Processing stopped!')
                return
            fps = v.fps if v.fps > 0 else util.detect_fps(v.filename)
            if v.endframe == 0:
                v.endframe = get_video_frame_total(v.filename)

            update_status(f'Creating {os.path.basename(v.finalname)} with {fps} FPS...')
            start_processing = time()
            if roop.globals.keep_frames or not use_new_method:
                util.create_temp(v.filename)
                update_status('Extracting frames...')
                ffmpeg.extract_frames(v.filename,v.startframe,v.endframe, fps)
                if not roop.globals.processing:
                    end_processing('Processing stopped!')
                    return

                temp_frame_paths = util.get_temp_frame_paths(v.filename)
                process_mgr.run_batch(temp_frame_paths, temp_frame_paths, roop.globals.execution_threads)
                if not roop.globals.processing:
                    end_processing('Processing stopped!')
                    return
                if roop.globals.wait_after_extraction:
                    extract_path = os.path.dirname(temp_frame_paths[0])
                    util.open_folder(extract_path)
                    input("Press any key to continue...")
                    print("Resorting frames to create video")
                    util.sort_rename_frames(extract_path)                                    
                
                ffmpeg.create_video(v.filename, f.finalname, fps)
                if not roop.globals.keep_frames:
                    util.delete_temp_frames(temp_frame_paths[0])
            else:
                if util.has_extension(v.filename, ['gif']):
                    skip_audio = True
                else:
                    skip_audio = roop.globals.skip_audio
                process_mgr.run_batch_inmem(v.filename, v.finalname, v.startframe, v.endframe, fps,roop.globals.execution_threads, skip_audio)
                
            if not roop.globals.processing:
                end_processing('Processing stopped!')
                return
            
            video_file_name = v.finalname
            if os.path.isfile(video_file_name):
                destination = ''
                if util.has_extension(v.filename, ['gif']):
                    gifname = util.get_destfilename_from_path(v.filename, roop.globals.output_path, '.gif')
                    destination = util.replace_template(gifname, index=index)
                    pathlib.Path(os.path.dirname(destination)).mkdir(parents=True, exist_ok=True)

                    update_status('Creating final GIF')
                    ffmpeg.create_gif_from_video(video_file_name, destination)
                    if os.path.isfile(destination):
                        os.remove(video_file_name)
                else:
                    skip_audio = roop.globals.skip_audio
                    destination = util.replace_template(video_file_name, index=index)
                    pathlib.Path(os.path.dirname(destination)).mkdir(parents=True, exist_ok=True)
                    #-------------------------------new
                    shutil.move(video_file_name, destination)
                    #os.remove(video_file_name)
                    #------------------------------
                    #if not skip_audio:
                        #ffmpeg.restore_audio(video_file_name, v.filename, v.startframe, v.endframe, destination)
                        #print("video_file_name="+video_file_name)
                        #if os.path.isfile(destination):
                            #print("destination="+destination)                            
                            #os.remove(video_file_name)
                    #else:
                        #shutil.move(video_file_name, destination)
                        #os.remove(video_file_name)
                #print("destination="+destination)
                encryption(destination)
                update_status(f'\nProcessing {os.path.basename(destination)} took {time() - start_processing} secs')

            else:
                update_status(f'Failed processing {os.path.basename(v.finalname)}!')
    end_processing('Finished')


def end_processing(msg:str):
    update_status(msg)
    roop.globals.target_folder_path = None
    release_resources()


def destroy() -> None:
    if roop.globals.target_path:
        util.clean_temp(roop.globals.target_path)
    release_resources()        
    sys.exit()


def run() -> None:
    parse_args()
    if not pre_check():
        return
    roop.globals.CFG = Settings('config.yaml')
    roop.globals.execution_threads = roop.globals.CFG.max_threads
    roop.globals.video_encoder = roop.globals.CFG.output_video_codec
    roop.globals.video_quality = roop.globals.CFG.video_quality
    roop.globals.max_memory = roop.globals.CFG.memory_limit if roop.globals.CFG.memory_limit > 0 else None
    ui.run()
