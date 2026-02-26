import streamlit as st
import os
import tempfile
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import numpy as np
from moviepy.editor import AudioFileClip, ImageClip, CompositeVideoClip, concatenate_audioclips
from aip import AipSpeech

# --- [1. 环境配置与密钥读取] ---
try:
    # 严格从 Streamlit Secrets 读取
    APP_ID = str(st.secrets["baidu_api"]["app_id"])
    API_KEY = str(st.secrets["baidu_api"]["api_key"])
    SECRET_KEY = str(st.secrets["baidu_api"]["secret_key"])
except Exception:
    st.error("❌ Secrets 配置缺失！请在后台配置 [baidu_api]")
    st.stop()

client = AipSpeech(APP_ID, API_KEY, SECRET_KEY)

# --- [2. Pillow 文字渲染函数 (绕过 ImageMagick 限制)] ---
def create_text_image(text, fontsize, color, font_path, size=(720, 1280), line_spacing=15):
    """手动绘制文字图片，返回 numpy 数组供 MoviePy 使用"""
    # 创建透明背景图
    img = PIL.Image.new('RGBA', size, (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(img)
    
    try:
        font = PIL.ImageFont.truetype(font_path, fontsize)
    except:
        font = PIL.ImageFont.load_default()

    lines = text.split('\n')
    # 计算每一行的高度和宽度
    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    
    total_content_height = sum(line_heights) + line_spacing * (len(lines) - 1)
    current_y = (size[1] - total_content_height) // 2  # 垂直居中
    
    for i, line in enumerate(lines):
        x = (size[0] - line_widths[i]) // 2  # 水平居中
        # 绘制简单描边增加可读性
        for off in [(-1,-1), (1,-1), (-1,1), (1,1)]:
            draw.text((x+off[0], current_y+off[1]), line, font=font, fill="black")
        draw.text((x, current_y), line, font=font, fill=color)
        current_y += line_heights[i] + line_spacing

    return np.array(img)

# --- [3. 音频处理逻辑] ---
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
        # 克隆原声作为静音停顿
        silence_dur = min(0.6, line_audio.duration / 2)
        clips.append(line_audio.subclip(0, silence_dur).volumex(0))
    
    if not clips: return None, []
    return concatenate_audioclips(clips), temp_files

# --- [4. 视频合成逻辑] ---
def make_video_one_image(title_text, content_text, image_file):
    audio, temp_audio_files = get_mixed_audio_safe_pause(content_text)
    if not audio: return None

    duration = audio.duration + 0.5
    temp_audio_path = "temp_voice_final.mp3"
    audio.write_audiofile(temp_audio_path, fps=44100, logger=None)

    # 字体路径：确保仓库根目录有 simhei.ttf
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    # 处理背景图
    file_ext = os.path.splitext(image_file.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_img:
        tmp_img.write(image_file.getvalue())
        img_path = tmp_img.name

    try:
        # 1. 背景层
        bg_clip = ImageClip(img_path).set_duration(duration).resize(height=1280)
        if bg_clip.w < 720: bg_clip = bg_clip.resize(width=720)
        bg_clip = bg_clip.set_position("center")

        # 2. 标题层 (Pillow 绘制图片后转 ImageClip)
        video_elements = [bg_clip]
        if title_text.strip():
            title_img = create_text_image(title_text, 45, "yellow", font_path)
            # 标题位置稍微靠上
            title_clip = ImageClip(title_img).set_duration(duration).set_position(('center', -280))
            video_elements.append(title_clip)

        # 3. 正文层 (Pillow 绘制)
        content_img = create_text_image(content_text, 30, "white", font_path, line_spacing=12)
        txt_clip = ImageClip(content_img).set_duration(duration).set_position('center')
        video_elements.append(txt_clip)

        # 4. 合成
        final_video = CompositeVideoClip(video_elements, size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
        output_name = "final_output.mp4"
        final_video.write_videofile(output_name, fps=24, codec="libx264", audio_codec="aac", logger=None)
        
        audio.close()
        return output_name

    finally:
        if os.path.exists(img_path): os.remove(img_path)
        if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
        for f in temp_audio_files:
            if os.path.exists(f): os.remove(f)

# --- [5. Streamlit 界面] ---
st.set_page_config(page_title="短语课件生成器")
st.title("🎬 视频助手 (Pillow 稳定版)")

user_title = st.text_input("💎 标题 (不朗读):", "中考英语万能搭配")
user_content = st.text_area("✍️ 正文 (带停顿朗读):", "a good way to improve English\n提高英语的好方法", height=200)
bg_upload = st.file_uploader("📸 背景图:", type=["jpg", "png", "jpeg"])

if st.button("🚀 生成视频成品"):
    if user_content and bg_upload:
        with st.spinner("正在通过 Pillow 渲染并合成视频..."):
            try:
                res = make_video_one_image(user_title, user_content, bg_upload)
                if res:
                    st.video(res)
                    with open(res, "rb") as f:
                        st.download_button("📥 下载视频", f, "final.mp4")
            except Exception as e:
                st.error(f"出错详情: {e}")
