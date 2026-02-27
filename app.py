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

# --- [3. 像素级自动换行渲染] ---
def create_text_image(text, fontsize, color, font_path, size=(720, 1280), line_spacing=12):
    img = PIL.Image.new('RGBA', size, (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(img)
    try:
        font = PIL.ImageFont.truetype(font_path, fontsize)
    except:
        font = PIL.ImageFont.load_default()

    max_width = 600  # 画布 720，留出两侧各 60 像素的边距
    paragraphs = text.split('\n')
    final_lines = []

    # 动态计算换行
    for para in paragraphs:
        if not para.strip():
            final_lines.append("")
            continue
        
        words = list(para) # 按字符拆分，以支持中英混排
        current_line = ""
        for char in words:
            test_line = current_line + char
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                current_line = test_line
            else:
                final_lines.append(current_line)
                current_line = char
        final_lines.append(current_line)

    # 计算总高度并居中绘制
    line_data = []
    total_h = 0
    for line in final_lines:
        bbox = draw.textbbox((0, 0), line if line else " ", font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        line_data.append((line, w, h))
        total_h += h + line_spacing

    current_y = (size[1] - total_h) // 2
    for line, w, h in line_data:
        if line:
            x = (size[0] - w) // 2
            for off in [(-1,-1), (1,-1), (-1,1), (1,1)]:
                draw.text((x+off[0], current_y+off[1]), line, font=font, fill="black")
            draw.text((x, current_y), line, font=font, fill=color)
        current_y += h + line_spacing

    return np.array(img)

# --- [4. 视频生成逻辑] ---
def make_video_one_image(title_text, content_text, image_source, client):
    # 朗读逻辑：按用户输入的自然段落请求语音
    paragraphs = [p.strip() for p in content_text.split('\n') if p.strip()]
    clips = []
    temp_files = []
    
    for i, p in enumerate(paragraphs):
        is_chinese = any('\u4e00' <= char <= '\u9fff' for char in p)
        # 百度单次请求上限约 512 汉字，长段落建议用户手动换行，但这里保证了显示的自动折行
        params = {"vol": 5, "spd": 5, "pit": 5, "per": 0, "aue": 3} if is_chinese else \
                 {"vol": 5, "spd": 4, "pit": 5, "per": 4179, "aue": 3}
        
        res = client.synthesis(p, "zh" if is_chinese else "en", 1, params)
        if isinstance(res, dict): continue

        tmp_name = f"v_{i}.mp3"
        with open(tmp_name, "wb") as f: f.write(res)
        temp_files.append(tmp_name)
        
        audio_clip = AudioFileClip(tmp_name)
        clips.append(audio_clip)
        # 段落间加 0.8s 停顿，更自然
        silence = audio_clip.subclip(0, min(0.8, audio_clip.duration)).volumex(0)
        clips.append(silence)
    
    if not clips: return None
    final_audio = concatenate_audioclips(clips)
    duration = final_audio.duration + 0.5
    temp_audio_path = "final_voice.mp3"
    final_audio.write_audiofile(temp_audio_path, fps=44100, logger=None)
    
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    try:
        # 处理背景
        with PIL.Image.open(image_source) as img:
            img = img.convert("RGB")
            h_target = 1280
            w_target = int(img.size[0] * (h_target / img.size[1]))
            if w_target < 720: w_target = 720
            resized_bg = img.resize((w_target, h_target), RESAMPLE_MODE)
            bg_clip = ImageClip(np.array(resized_bg)).set_duration(duration).set_position("center")

        # 渲染
        title_img = create_text_image(title_text, 50, "yellow", font_path)
        content_img = create_text_image(content_text, 32, "white", font_path)
        
        clips_to_combine = [
            bg_clip,
            ImageClip(title_img).set_duration(duration).set_position(('center', -320)),
            ImageClip(content_img).set_duration(duration).set_position('center')
        ]

        final_video = CompositeVideoClip(clips_to_combine, size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
        output_name = "output.mp4"
        final_video.write_videofile(output_name, fps=24, codec="libx264", audio_codec="aac", logger=None)
        return output_name
    finally:
        if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
        for f in temp_files:
            if os.path.exists(f): os.remove(f)

# --- [5. 主界面] ---
def main():
    st.set_page_config(page_title="视频助手", layout="centered")
    if check_password():
        st.title("🎬 智能长文本视频助手")
        try:
            client = AipSpeech(str(st.secrets["baidu_api"]["app_id"]), 
                               str(st.secrets["baidu_api"]["api_key"]), 
                               str(st.secrets["baidu_api"]["secret_key"]))
        except: st.stop()

        user_title = st.text_input("💎 标题:", "长段落演示")
        user_content = st.text_area("✍️ 内容 (直接粘贴长段落即可):", height=300)
        bg_upload = st.file_uploader("📸 上传背景:", type=["jpg", "png", "jpeg"])

        if st.button("🚀 生成视频"):
            if not user_content.strip(): return
            img_src = bg_upload if bg_upload else ("default_bg.jpg" if os.path.exists("default_bg.jpg") else None)
            
            with st.spinner("正在智能排版长文本并生成语音..."):
                try:
                    res = make_video_one_image(user_title, user_content, img_src, client)
                    if res:
                        st.video(res)
                        with open(res, "rb") as f:
                            st.download_button("📥 下载视频", f, "final.mp4")
                except Exception as e:
                    st.error(f"出错: {e}")

if __name__ == "__main__":
    main()
