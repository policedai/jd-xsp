import streamlit as st
import os
import tempfile
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import numpy as np
from moviepy.editor import AudioFileClip, ImageClip, CompositeVideoClip, concatenate_audioclips
from aip import AipSpeech

# --- [1. 兼容性补丁：解决新版 Pillow 移除 ANTIALIAS 的问题] ---
if hasattr(PIL.Image, 'Resampling'):
    RESAMPLE_MODE = PIL.Image.Resampling.LANCZOS
else:
    RESAMPLE_MODE = getattr(PIL.Image, 'ANTIALIAS', PIL.Image.BICUBIC)

# --- [2. 环境配置与密钥读取] ---
try:
    # 从 Streamlit Secrets 安全读取
    APP_ID = str(st.secrets["baidu_api"]["app_id"])
    API_KEY = str(st.secrets["baidu_api"]["api_key"])
    SECRET_KEY = str(st.secrets["baidu_api"]["secret_key"])
except Exception:
    st.error("❌ Secrets 配置缺失！请在 Streamlit 后台 Settings -> Secrets 中添加 [baidu_api] 相关内容。")
    st.stop()

client = AipSpeech(APP_ID, API_KEY, SECRET_KEY)

# --- [3. Pillow 文字渲染函数 (绕过 ImageMagick 限制)] ---
def create_text_image(text, fontsize, color, font_path, size=(720, 1280), line_spacing=15):
    """手动绘制文字图片，返回 numpy 数组供 MoviePy 使用"""
    img = PIL.Image.new('RGBA', size, (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(img)
    
    try:
        font = PIL.ImageFont.truetype(font_path, fontsize)
    except:
        font = PIL.ImageFont.load_default()

    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if not lines: return np.array(img)

    # 计算文本总高度以进行垂直居中
    line_metrics = []
    total_height = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        line_metrics.append((w, h))
        total_height += h
    
    total_height += line_spacing * (len(lines) - 1)
    current_y = (size[1] - total_height) // 2 
    
    for i, line in enumerate(lines):
        w, h = line_metrics[i]
        x = (size[0] - w) // 2  # 水平居中
        # 简单文字描边
        for off in [(-1,-1), (1,-1), (-1,1), (1,1)]:
            draw.text((x+off[0], current_y+off[1]), line, font=font, fill="black")
        draw.text((x, current_y), line, font=font, fill=color)
        current_y += h + line_spacing

    return np.array(img)

# --- [4. 核心音频处理] ---
def get_mixed_audio_safe_pause(text):
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    clips = []
    temp_files = []
    
    for i, line in enumerate(lines):
        is_chinese = any('\u4e00' <= char <= '\u9fff' for char in line)
        params = {"vol": 5, "spd": 5, "pit": 5, "per": 0, "aue": 3} if is_chinese else \
                 {"vol": 5, "spd": 4, "pit": 5, "per": 4179, "aue": 3}
        
        res = client.synthesis(line, "zh" if is_chinese else "en", 1, params)
        if isinstance(res, dict): continue

        tmp_name = f"v_{i}.mp3"
        with open(tmp_name, "wb") as f:
            f.write(res)
        temp_files.append(tmp_name)
        
        line_audio = AudioFileClip(tmp_name)
        clips.append(line_audio)
        # 0.6s 停顿
        silence_dur = min(0.6, line_audio.duration / 2)
        clips.append(line_audio.subclip(0, silence_dur).volumex(0))
    
    if not clips: return None, []
    return concatenate_audioclips(clips), temp_files

# --- [5. 视频合成逻辑] ---
def make_video_one_image(title_text, content_text, image_file):
    audio, temp_audio_files = get_mixed_audio_safe_pause(content_text)
    if not audio: return None

    duration = audio.duration + 0.5
    temp_audio_path = "temp_voice_final.mp3"
    audio.write_audiofile(temp_audio_path, fps=44100, logger=None)

    # 字体路径：GitHub 根目录必须有 simhei.ttf
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    file_ext = os.path.splitext(image_file.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_img:
        tmp_img.write(image_file.getvalue())
        img_path = tmp_img.name

    try:
        # 1. 背景层：手动使用 Pillow 缩放以避开 MoviePy 内部的 ANTIALIAS 错误
        with PIL.Image.open(img_path) as raw_img:
            raw_img = raw_img.convert("RGB")
            h_target = 1280
            w_target = int(raw_img.size[0] * (h_target / raw_img.size[1]))
            if w_target < 720: w_target = 720
            resized_bg = raw_img.resize((w_target, h_target), RESAMPLE_MODE)
            bg_clip = ImageClip(np.array(resized_bg)).set_duration(duration).set_position("center")

        video_elements = [bg_clip]

        # 2. 标题层 (Pillow 渲染图片)
        if title_text.strip():
            title_img = create_text_image(title_text, 50, "yellow", font_path)
            # 位置向上偏移，避免挡住正文
            title_clip = ImageClip(title_img).set_duration(duration).set_position(('center', -300))
            video_elements.append(title_clip)

        # 3. 正文层 (Pillow 渲染图片)
        content_img = create_text_image(content_text, 32, "white", font_path, line_spacing=15)
        txt_clip = ImageClip(content_img).set_duration(duration).set_position('center')
        video_elements.append(txt_clip)

        # 4. 合成最终视频
        final_video = CompositeVideoClip(video_elements, size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
        output_name = "final_output.mp4"
        final_video.write_videofile(output_name, fps=24, codec="libx264", audio_codec="aac", logger=None)
        
        audio.close()
        return output_name

    finally:
        if os.path.exists(img_path): os.remove(img_path)
        if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
        for f in temp_audio_files:
            if os.path.exists(f): 
                try: os.remove(f)
                except: pass

# --- [6. Streamlit 界面] ---
st.set_page_config(page_title="中英短语视频助手")
st.title("🎬 课件视频助手 (兼容稳定版)")

user_title = st.text_input("💎 视频标题 (只显示不朗读):", "英语万能搭配")
user_content = st.text_area("✍️ 朗读内容 (带停顿):", "a good way to learn English\n学习英语的好方法", height=200)
bg_upload = st.file_uploader("📸 上传背景图:", type=["jpg", "png", "jpeg"])

if st.button("🚀 开始制作视频"):
    if user_content and bg_upload:
        with st.spinner("正在渲染视频，请耐心等待..."):
            try:
                res = make_video_one_image(user_title, user_content, bg_upload)
                if res:
                    st.video(res)
                    with open(res, "rb") as f:
                        st.download_button("📥 下载成品视频", f, "course_video.mp4")
            except Exception as e:
                st.error(f"渲染过程中发生错误: {e}")
