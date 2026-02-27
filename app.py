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

# --- [3. 紧凑型流式文字渲染] ---
def create_full_content_image(title, content, font_path, size=(720, 1280)):
    # 预设画布测量
    temp_img = PIL.Image.new('RGBA', (720, 3000), (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(temp_img)
    
    title_size = 50
    content_size = 32
    max_w = 640  # 左右留边
    line_spacing = 10
    
    title_font = PIL.ImageFont.truetype(font_path, title_size)
    content_font = PIL.ImageFont.truetype(font_path, content_size)

    # 处理逻辑：将所有行存入一个列表，记录其字体和类型
    all_lines_to_draw = []
    
    # 1. 处理标题换行
    if title.strip():
        curr_t = ""
        for char in list(title):
            test_t = curr_t + char
            if draw.textbbox((0, 0), test_t, font=title_font)[2] <= max_w:
                curr_t = test_t
            else:
                all_lines_to_draw.append((curr_t, title_font, "yellow"))
                curr_t = char
        all_lines_to_draw.append((curr_t, title_font, "yellow"))
        all_lines_to_draw.append(("", None, 20)) # 标题和正文之间的额外空隙

    # 2. 处理正文自动折行
    paragraphs = content.split('\n')
    for para in paragraphs:
        if not para.strip():
            all_lines_to_draw.append(("", None, 10))
            continue
        curr_c = ""
        for char in list(para):
            test_c = curr_c + char
            if draw.textbbox((0, 0), test_c, font=content_font)[2] <= max_w:
                curr_c = test_c
            else:
                all_lines_to_draw.append((curr_c, content_font, "white"))
                curr_c = char
        all_lines_to_draw.append((curr_c, content_font, "white"))

    # 3. 计算总高度
    total_h = 0
    draw_data = []
    for line_text, font, color_or_space in all_lines_to_draw:
        if font is None: # 这是一个间距
            total_h += color_or_space
            draw_data.append((None, None, color_or_space))
        else:
            bbox = draw.textbbox((0, 0), line_text if line_text else " ", font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw_data.append((line_text, font, color_or_space, w, h))
            total_h += h + line_spacing

    # 4. 开始绘制 (保持紧凑排列)
    res_img = PIL.Image.new('RGBA', (720, total_h + 100), (255, 255, 255, 0))
    res_draw = PIL.ImageDraw.Draw(res_img)
    
    curr_y = 20 # 起始留白
    for data in draw_data:
        if data[0] is None: # 间距层
            curr_y += data[2]
        else:
            line_text, font, color, w, h = data
            x = (720 - w) // 2
            # 描边
            for off in [(-1,-1), (1,-1), (-1,1), (1,1)]:
                res_draw.text((x+off[0], curr_y+off[1]), line_text, font=font, fill="black")
            res_draw.text((x, curr_y), line_text, font=font, fill=color)
            curr_y += h + line_spacing

    return np.array(res_img)

# --- [4. 视频合成] ---
def make_video_one_image(title_text, content_text, image_source, client):
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
        clips.append(c.subclip(0, min(0.6, c.duration)).volumex(0))
    
    if not clips: return None
    final_audio = concatenate_audioclips(clips)
    duration = final_audio.duration + 0.5
    temp_audio_path = "final.mp3"
    final_audio.write_audiofile(temp_audio_path, fps=44100, logger=None)
    
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    try:
        # 背景
        with PIL.Image.open(image_source) as img:
            img = img.convert("RGB")
            resized_bg = img.resize((int(img.size[0] * (1280 / img.size[1])), 1280), RESAMPLE_MODE)
            bg_clip = ImageClip(np.array(resized_bg)).set_duration(duration).set_position("center")

        # 生成合体后的文字层 (标题+正文)
        full_text_arr = create_full_content_image(title_text, content_text, font_path)
        
        # 摆放位置：从顶部 1/10 处开始放，确保“紧接其后”
        text_clip = ImageClip(full_text_arr).set_duration(duration).set_position(('center', 120))

        final_video = CompositeVideoClip([bg_clip, text_clip], size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
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
        st.title("🎬 紧凑排版视频助手")
        client = AipSpeech(str(st.secrets["baidu_api"]["app_id"]), str(st.secrets["baidu_api"]["api_key"]), str(st.secrets["baidu_api"]["secret_key"]))
        
        u_title = st.text_input("💎 标题:", "英语美文")
        u_content = st.text_area("✍️ 朗读内容:", height=300)
        u_bg = st.file_uploader("📸 上传背景:", type=["jpg", "png", "jpeg"])

        if st.button("🚀 生成视频"):
            if not u_content.strip(): return
            src = u_bg if u_bg else ("default_bg.jpg" if os.path.exists("default_bg.jpg") else None)
            with st.spinner("正在生成紧凑型课件..."):
                try:
                    res = make_video_one_image(u_title, u_content, src, client)
                    if res:
                        st.video(res)
                        with open(res, "rb") as f: st.download_button("📥 下载视频", f, "output.mp4")
                except Exception as e:
                    st.error(f"渲染出错: {e}")

if __name__ == "__main__":
    main()
