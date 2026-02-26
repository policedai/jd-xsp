import streamlit as st
import os
import tempfile
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import numpy as np
from moviepy.editor import AudioFileClip, ImageClip, CompositeVideoClip, concatenate_audioclips
from aip import AipSpeech

# --- [1. 纯密码登录验证] ---
def check_password():
    """如果返回 True，则用户输入了正确的密码"""
    def password_entered():
        """检查用户输入的密码是否正确"""
        if st.session_state["password"] == st.secrets["passwords"]["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # 不在内存中保留密码
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # 首次运行，显示密码输入框
        st.text_input("请输入访问密码", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        # 密码错误
        st.text_input("请输入访问密码", type="password", on_change=password_entered, key="password")
        st.error("😕 密码错误，请重新输入")
        return False
    else:
        # 密码正确
        return True

# --- [2. 图像处理补丁] ---
if hasattr(PIL.Image, 'Resampling'):
    RESAMPLE_MODE = PIL.Image.Resampling.LANCZOS
else:
    RESAMPLE_MODE = getattr(PIL.Image, 'ANTIALIAS', PIL.Image.BICUBIC)

def create_text_image(text, fontsize, color, font_path, size=(720, 1280), line_spacing=15):
    img = PIL.Image.new('RGBA', size, (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(img)
    try:
        font = PIL.ImageFont.truetype(font_path, fontsize)
    except:
        font = PIL.ImageFont.load_default()
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if not lines: return np.array(img)
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
        x = (size[0] - w) // 2
        for off in [(-1,-1), (1,-1), (-1,1), (1,1)]:
            draw.text((x+off[0], current_y+off[1]), line, font=font, fill="black")
        draw.text((x, current_y), line, font=font, fill=color)
        current_y += h + line_spacing
    return np.array(img)

# --- [3. 视频生成核心逻辑] ---
def get_mixed_audio_safe_pause(text, client):
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
        silence_dur = min(0.6, line_audio.duration / 2)
        clips.append(line_audio.subclip(0, silence_dur).volumex(0))
    if not clips: return None, []
    return concatenate_audioclips(clips), temp_files

def make_video_one_image(title_text, content_text, image_file, client):
    audio, temp_audio_files = get_mixed_audio_safe_pause(content_text, client)
    if not audio: return None
    duration = audio.duration + 0.5
    temp_audio_path = "temp_voice_final.mp3"
    audio.write_audiofile(temp_audio_path, fps=44100, logger=None)
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"
    file_ext = os.path.splitext(image_file.name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp_img:
        tmp_img.write(image_file.getvalue())
        img_path = tmp_img.name
    try:
        with PIL.Image.open(img_path) as raw_img:
            raw_img = raw_img.convert("RGB")
            h_target = 1280
            w_target = int(raw_img.size[0] * (h_target / raw_img.size[1]))
            if w_target < 720: w_target = 720
            resized_bg = raw_img.resize((w_target, h_target), RESAMPLE_MODE)
            bg_clip = ImageClip(np.array(resized_bg)).set_duration(duration).set_position("center")
        video_elements = [bg_clip]
        if title_text.strip():
            title_img = create_text_image(title_text, 50, "yellow", font_path)
            title_clip = ImageClip(title_img).set_duration(duration).set_position(('center', -300))
            video_elements.append(title_clip)
        content_img = create_text_image(content_text, 32, "white", font_path, line_spacing=15)
        txt_clip = ImageClip(content_img).set_duration(duration).set_position('center')
        video_elements.append(txt_clip)
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

# --- [4. 主程序入口] ---
def main():
    st.set_page_config(page_title="内部制作工具", layout="centered")
    
    if check_password():
        # --- 登录成功后的内容 ---
        st.title("🎬 内部专用视频助手")
        
        try:
            client = AipSpeech(
                str(st.secrets["baidu_api"]["app_id"]),
                str(st.secrets["baidu_api"]["api_key"]),
                str(st.secrets["baidu_api"]["secret_key"])
            )
        except Exception:
            st.error("密钥未配置")
            st.stop()

        user_title = st.text_input("💎 标题:", "中考英语万能搭配")
        user_content = st.text_area("✍️ 朗读正文:", "a good way to learn English\n学习英语的好方法", height=200)
        bg_upload = st.file_uploader("📸 背景图:", type=["jpg", "png", "jpeg"])

        if st.button("🚀 生成视频成品"):
            if user_content and bg_upload:
                with st.spinner("处理中..."):
                    res = make_video_one_image(user_title, user_content, bg_upload, client)
                    if res:
                        st.video(res)
                        with open(res, "rb") as f:
                            st.download_button("📥 下载视频", f, "output.mp4")

if __name__ == "__main__":
    main()
