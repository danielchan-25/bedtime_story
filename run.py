# -*- coding: utf-8 -*-
import os
import sys
import requests
from PIL import Image
import io
import base64
import asyncio
from translate import Translator
import json
import edge_tts
import logging
import subprocess
import configparser
import datetime

# --------------------------------- #
# Date: 2024-02-20
# Author: AlexChing
# --------------------------------- #

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger()
now_time = datetime.datetime.today().strftime('%Y-%m-%d_%H%M%S')

config = configparser.ConfigParser()
config.read('config.ini')
image_dir = config['global']['image_dir']
audio_dir = config['global']['audio_dir']
video_dir = config['global']['video_dir']
video_txt = config['global']['video_txt']
done_video_dir = config['global']['done_video_dir']
chatglm_api_address = config['global']['chatglm_api_address']
stablediffusion_api_address = config['global']['stablediffusion_api_address']


def check_env():
    logger.info('开始检查当前环境...')
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            subprocess.run(['ffprobe', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.info(f'环境检查正常。')
        except FileNotFoundError:
            logger.error('未检测到 ffprobe。')
            sys.exit(1)
    except FileNotFoundError:
        logger.error('未检测到 ffmpeg。')
        sys.exit(1)

# 生成故事内容
def get_content(prompt):
    content_en = [] # 中译英

    logger.info(f'正在输入Promtps: {prompt}')
    headers = {'Content-Type': 'application/json'}
    payload = {'prompt': f'{prompt}', 'history': []}
    response = requests.post(url=chatglm_api_address, headers=headers, json=payload)

    if response.status_code == 200:
        content = json.loads(response.text)['response']
        content_list = content.split('\n\n', -1)  # 内容分段
        if '睡前故事' in content_list[0]:
            content_list = content_list[1:]

        for i in content_list:
            translator = Translator(from_lang="zh", to_lang="en")
            translated_text = translator.translate(i)
            content_en.append(translated_text)

        logger.info(f'文章生成成功')
        return content_list, content_en
    else:
        logger.error(f'文章生成失败，请检查ChatGLM API是否正常。')
        sys.exit(1)

# 文生图
def transfer_sdapi(prompt):
    logger.info('开始生成图片...')
    if type(prompt) is list:
        for index, i in enumerate(prompt):
            payload = {
                'override_settings': {
                    'sd_model_checkpoint': config['stablediffusion']['sd_model_checkpoint'],
                },
                'prompt': i,  # 正向提示词
                'negative_prompt': f'{config["stablediffusion"]["negative_prompt"]}',  # 反向提示词
                'sampler_name': config['stablediffusion']['sampler_name'],
                'steps': config['stablediffusion']['steps'],
                'width': config['stablediffusion']['width'],
                'height': config['stablediffusion']['height'],
                'CLIP_stop_at_last_layers': config['stablediffusion']['CLIP_stop_at_last_layers'],
                'batch_size': config['stablediffusion']['batch_size'],
                'seed': config['stablediffusion']['seed']
            }

            response = requests.post(url=f'{stablediffusion_api_address}/sdapi/v1/txt2img', json=payload)
            if response.status_code == 200:
                logger.info(f'正在生成第{int(index) + 1}图片...')
                r = response.json()
                image = Image.open(io.BytesIO(base64.b64decode(r['images'][0])))
                image.save(f'{image_dir}output_{index}.jpg')
            else:
                logger.error(f'连接 Stable Diffusion API 失败')
    else:
        logger.error(f'{prompt}格式错误，应为列表格式。')

# 文字配音
def content_dubbing(content):
    logger.info('开始生成配音...')
    voice = config['edge_tts']['voice']

    if type(content) is list:
        async def save(content, output_file):
            communicate = edge_tts.Communicate(content, voice)
            await communicate.save(output_file)

        async def save_audio():
            for index, i in enumerate(content):
                logger.info(f'正在给第{int(index) + 1}句配音...')
                output_file = f'{audio_dir}output_{index}.mp3'
                await save(i, output_file)

        asyncio.run(save_audio())

    else:
        logger.error(f'{content}格式错误，应为列表格式。')

# 图音合并
def merge():
    # 获取音频时长，用于限制视频时长
    def get_audio_time(audio_file):
        if audio_file.endswith('mp3'):
            command = ['ffprobe', '-i', f'{audio_dir}{audio_file}', '-show_entries', 'format=duration', '-v', 'quiet',
                       '-of', 'csv=p=0']
            output = subprocess.check_output(command, encoding='utf-8')
            audio_time = float(output)
            return audio_time

    # 单个视频合成
    def merge_video(audio_file, image_file):
        output_file = audio_file.split('.mp3', -1)[0] + '.mp4'  # 视频命名
        audio_time = get_audio_time(audio_file)
        command = [
            'ffmpeg',
            '-loop', '1',  # 循环播放图片
            '-i', f'{image_dir}{image_file}',  # 图片路径
            '-i', f'{audio_dir}{audio_file}',  # 音频路径
            '-c:v', 'libx264',  # 视频编码器
            '-c:a', 'aac',  # 音频编码器
            '-strict', 'experimental',  # 启用实验性特性
            '-b:a', '192k',  # 音频比特率
            '-t', str(audio_time),  # 视频持续时间
            '-vf', 'fps=25',  # 视频帧率
            f'{video_dir}{output_file}'  # 输出文件路径
        ]
        subprocess.run(command)
        logger.info(f'正在对 {audio_file}, {image_file} 进行合成...')

    audio_list = [i for i in os.listdir(audio_dir)]
    image_list = [i for i in os.listdir(image_dir)]
    for i in range(len(audio_list)):
        merge_video(audio_file=audio_list[i], image_file=image_list[i])

    # 所有单个视频合成
    def merge_all():
        logger.info(f'正在合成所有单个视频...')
        file = open(video_txt, 'w')
        for i in os.listdir(video_dir):
            file.write('file ' + "video/" + i + '\n')
        file.close()
        command = ['ffmpeg', '-f', 'concat', '-safe', '0', '-i', 'output\\video.txt', '-c', 'copy',
                   f'{done_video_dir}output_{now_time}.mp4']
        subprocess.run(command)
        logger.info(f'视频合成完毕，具体见：{done_video_dir}')

    merge_all()

    # 删除旧的素材
    def delete_files(dir):
        for i in os.listdir(dir):
            file_path = os.path.join(dir, i)
            if os.path.isfile(file_path):
                os.remove(file_path)

    file_dir_list = [image_dir, audio_dir, video_dir]
    [delete_files(i) for i in file_dir_list]

def main():
    check_env()
    prompt = '请生成一个适合0-3岁儿童的睡前故事'
    content,content_en = get_content(prompt=prompt)
    transfer_sdapi(prompt=content_en)
    content_dubbing(content=content)
    merge()

if __name__ == '__main__':
    main()
