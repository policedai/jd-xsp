import streamlit as st
import os
import tempfile
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import numpy as np
from moviepy.editor import AudioFileClip, ImageClip, CompositeVideoClip, concatenate_audioclips
from aip import AipSpeech

# --- [1. 登录验证] ---
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets["passwords"]["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False
    if "password_correct" not in st.session_state:
        st.text_input("请输入访问密码", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("请输入访问密码", type="password", on_change=password_entered, key="password")
        st.error("😕 密码错误")
        return False
    return True

# --- [2. 图像处理补丁] ---
if hasattr(PIL.Image, 'Resampling'):
    RESAMPLE_MODE = PIL.Image.Resampling.LANCZOS
else:
    RESAMPLE_MODE = getattr(PIL.Image, 'ANTIALIAS', PIL.Image.BICUBIC)

# --- [3. 文字渲染：生成自适应大小的透明文字图片] ---
def create_text_image(text, fontsize, color, font_path, max_w=620, line_spacing=12):
    # 预设一个大画布用于测量
    temp_img = PIL.Image.new('RGBA', (1200, 2400), (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(temp_img)
    try:
        font = PIL.ImageFont.truetype(font_path, fontsize)
    except:
        font = PIL.ImageFont.load_default()

    # 自动折行逻辑
    paragraphs = text.split('\n')
    final_lines = []
    for para in paragraphs:
        if not para.strip():
            final_lines.append("")
            continue
        current_line = ""
        for char in list(para):
            test_line = current_line + char
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if (bbox[2] - bbox[0]) <= max_w:
                current_line = test_line
            else:
                final_lines.append(current_line)
                current_line = char
        final_lines.append(current_line)

    # 计算实际需要的图片尺寸
    line_metrics = []
    max_actual_w = 0
    total_h = 0
    for line in final_lines:
        bbox = draw.textbbox((0, 0), line if line else " ", font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        line_metrics.append((line, w, h))
        max_actual_w = max(max_actual_w, w)
        total_h += h + line_spacing

    if total_h == 0: return None

    # 创建刚好大小的图片
    res_img = PIL.Image.new('RGBA', (max_actual_w + 20, total_h + 10), (255, 255, 255, 0))
    res_draw = PIL.ImageDraw.Draw(res_img)
    
    curr_y = 0
    for line, w, h in line_metrics:
        x = (max_actual_w + 20 - w) // 2
        if line:
            # 黑色描边增强文字清晰度
            for off in [(-1,-1), (1,-1), (-1,1), (1,1)]:
                res_draw.text((x+off[0], curr_y+off[1]), line, font=font, fill="black")
            res_draw.text((x, curr_y), line, font=font, fill=color)
        curr_y += h + line_spacing

    return np.array(res_img)

# --- [4. 视频合成核心] ---
def make_video_one_image(title_text, content_text, image_source, client):
    # 语音部分
    paragraphs = [p.strip() for p in content_text.split('\n') if p.strip()]
    clips, temp_files = [], []
    for i, p in enumerate(paragraphs):
        is_zh = any('\u4e00' <= char <= '\u9fff' for char in p)
        res = client.synthesis(p, "zh" if is_zh else "en", 1, {"vol":5,"spd":5,"pit":5,"per":0 if is_zh else 4179})
        if isinstance(res, dict): continue
        tmp = f"v_{i}.mp3"
        with open(tmp, "wb") as f: f.write(res)
        temp_files.append(tmp)
        c = AudioFileClip(tmp)
        clips.append(c)
        clips.append(c.subclip(0, min(0.6, c.duration)).volumex(0)) # 停顿
    
    if not clips: return None
    final_audio = concatenate_audioclips(clips)
    duration = final_audio.duration + 0.5
    temp_audio_path = "final_voice.mp3"
    final_audio.write_audiofile(temp_audio_path, fps=44100, logger=None)
    
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    try:
        # 背景
        with PIL.Image.open(image_source) as img:
            img = img.convert("RGB")
            # 缩放到 720x1280 比例
            resized_bg = img.resize((int(img.size[0] * (1280 / img.size[1])), 1280), RESAMPLE_MODE)
            bg_clip = ImageClip(np.array(resized_bg)).set_duration(duration).set_position("center")

        video_elements = [bg_clip]
        
        # 1. 标题 (位置设在 1/6 处，约 210px)
        if title_text.strip():
            title_arr = create_text_image(title_text, 55, "yellow", font_path, max_w=650)
            if title_arr is not None:
                # 顶部偏移 210 像素
                title_clip = ImageClip(title_arr).set_duration(duration).set_position(('center', 210))
                video_elements.append(title_clip)

        # 2. 正文 (位置设在中间偏下一点，约 500px，确保不与标题重叠)
        content_arr = create_text_image(content_text, 36, "white", font_path, max_w=600)
        if content_arr is not None:
            # 顶部偏移 500 像素
            content_clip = ImageClip(content_arr).set_duration(duration).set_position(('center', 500))
            video_elements.append(content_clip)

        # 合成
        final_video = CompositeVideoClip(video_elements, size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
        output_name = "final_output.mp4"
        final_video.write_videofile(output_name, fps=24, codec="libx264", audio_codec="aac", logger=None)
        return output_name
    finally:
        if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
        for f in temp_files:
            if os.path.exists(f): os.remove(f)

# --- [5. 界面] ---
def main():
    st.set_page_config(page_title="视频助手", layout="centered")
    if check_password():
        st.title("🎬 课件视频助手")
        client = AipSpeech(str(st.secrets["baidu_api"]["app_id"]), str(st.secrets["baidu_api"]["api_key"]), str(st.secrets["baidu_api"]["secret_key"]))
        
        u_title = st.text_input("💎 标题 (不朗读):", "课外英语拓展")
        u_content = st.text_area("✍️ 朗读内容 (支持长段落自动折行):", height=250)
        u_bg = st.file_uploader("📸 上传背景 (可选):", type=["jpg", "png", "jpeg"])

        if st.button("🚀 生成成品视频"):
            if not u_content.strip(): return
            src = u_bg if u_bg else ("default_bg.jpg" if os.path.exists("default_bg.jpg") else None)
            with st.spinner("视频合成中，请稍候..."):
                try:
                    res = make_video_one_image(u_title, u_content, src, client)
                    if res:
                        st.video(res)
                        with open(res, "rb") as f: st.download_button("📥 下载视频", f, "output_mp4")
                except Exception as e:
                    st.error(f"渲染出错: {e}")

if __name__ == "__main__":
    main()
