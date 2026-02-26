import streamlit as st
import os
import tempfile
import PIL.Image
import numpy as np
from moviepy.editor import TextClip, CompositeVideoClip, AudioFileClip, ImageClip, concatenate_audioclips
from moviepy.config import change_settings
from aip import AipSpeech

# --- [1. 兼容性补丁] ---
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

# --- [2. 环境配置与安全密钥] ---
# 自动检测本地 Windows 环境下的 ImageMagick
WINDOWS_MAGICK_PATH = r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"
if os.path.exists(WINDOWS_MAGICK_PATH):
    change_settings({"IMAGEMAGICK_BINARY": WINDOWS_MAGICK_PATH})

# 严格从 Secrets 读取百度密钥
try:
    APP_ID = str(st.secrets["baidu_api"]["app_id"])
    API_KEY = str(st.secrets["baidu_api"]["api_key"])
    SECRET_KEY = str(st.secrets["baidu_api"]["secret_key"])
except Exception:
    st.error("❌ Secrets 配置缺失！请在 Streamlit 后台 Settings -> Secrets 中添加 [baidu_api] 相关内容。")
    st.stop()

client = AipSpeech(APP_ID, API_KEY, SECRET_KEY)

# --- [3. 核心音频逻辑] ---
def get_mixed_audio_safe_pause(text):
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    clips = []
    temp_files = []
    
    for i, line in enumerate(lines):
        is_chinese = any('\u4e00' <= char <= '\u9fff' for char in line)
        # 百度合成参数
        params = {"vol": 5, "spd": 5, "pit": 5, "per": 0, "aue": 3} if is_chinese else \
                 {"vol": 5, "spd": 4, "pit": 5, "per": 4179, "aue": 3}
        lang = "zh" if is_chinese else "en"
        
        res = client.synthesis(line, lang, 1, params)

        if isinstance(res, dict):
            st.error(f"百度接口报错 (行 {i+1}): {res.get('error_msg')}")
            continue

        tmp_name = f"v_{i}.mp3"
        with open(tmp_name, "wb") as f:
            f.write(res)
        temp_files.append(tmp_name)
        
        line_audio = AudioFileClip(tmp_name)
        clips.append(line_audio)
        
        # 克隆原声作为 0.6s 静音（解决声道不匹配报错）
        silence_dur = min(0.6, line_audio.duration / 2)
        clips.append(line_audio.subclip(0, silence_dur).volumex(0))
    
    if not clips: return None, []
    return concatenate_audioclips(clips), temp_files

# --- [4. 视频生成逻辑] ---
def make_video_one_image(title_text, content_text, image_file):
    audio, temp_audio_files = get_mixed_audio_safe_pause(content_text)
    if not audio: return None

    duration = audio.duration + 0.5
    temp_audio_path = "temp_voice_final.mp3"
    audio.write_audiofile(temp_audio_path, fps=44100, logger=None)

    # 字体路径处理：务必确保 GitHub 仓库根目录有 simhei.ttf
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    # 处理上传的背景图
    file_ext = os.path.splitext(image_file.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_img:
        tmp_img.write(image_file.getvalue())
        img_path = tmp_img.name

    try:
        # 背景层
        bg_clip = ImageClip(img_path).set_duration(duration).resize(height=1280)
        if bg_clip.w < 720: bg_clip = bg_clip.resize(width=720)
        bg_clip = bg_clip.set_position("center")

        clips_to_combine = [bg_clip]

        # 标题层 (大字，不朗读)
        if title_text.strip():
            title_clip = TextClip(
                title_text, fontsize=45, color='yellow', font=font_path,
                method='caption', size=(600, None), align="center",
                stroke_color='black', stroke_width=1.5
            ).set_duration(duration).set_position(('center', 150))
            clips_to_combine.append(title_clip)

        # 正文层 (小字，紧密排版)
        txt_clip = TextClip(
            content_text, fontsize=30, color='white', font=font_path,
            method='caption', size=(600, None), align="center",
            stroke_color='black', stroke_width=1.0, interline=10
        ).set_duration(duration).set_position('center')
        clips_to_combine.append(txt_clip)

        # 合成最终视频
        final_video = CompositeVideoClip(clips_to_combine, size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
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

# --- [5. Streamlit 界面] ---
st.set_page_config(page_title="短语课件生成器")
st.title("🎬 视频课件生成器")

user_title = st.text_input("💎 标题 (显示不朗读):", "英语万能短语")
user_content = st.text_area("✍️ 正文 (朗读并带停顿):", "a good way to improve our English\n提高英语的好方法", height=200)
bg_upload = st.file_uploader("📸 上传背景图:", type=["jpg", "png", "jpeg"])

if st.button("🚀 生成视频成品"):
    if user_content and bg_upload:
        with st.spinner("渲染中，请稍候（约需1分钟）..."):
            try:
                res = make_video_one_image(user_title, user_content, bg_upload)
                if res:
                    st.video(res)
                    with open(res, "rb") as f:
                        st.download_button("📥 下载视频", f, "output.mp4")
            except Exception as e:
                st.error(f"渲染出错，请检查日志。报错详情: {e}")
