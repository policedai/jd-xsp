import streamlit as st
import os
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

# --- [3. 增强型文字渲染] ---
def create_full_content_image(title, content, font_path, settings):
    # 预设画布测量
    temp_img = PIL.Image.new('RGBA', (720, 5000), (255, 255, 255, 0))
    draw = PIL.ImageDraw.Draw(temp_img)
    
    title_size = settings['title_size']
    content_size = settings['content_size']
    max_w = 640  
    line_spacing = 12
    
    # 颜色获取
    color_map = {
        "鹅黄色": (255, 241, 67),
        "科技白": (245, 245, 245),
        "象牙金": (250, 218, 141),
        "护眼绿": (199, 237, 204),
        "天空蓝": (135, 206, 235),
        "烈焰红": (255, 69, 0)
    }
    selected_color = color_map.get(settings['text_color'], (255, 241, 67))
    title_color = (255, 255, 0) # 标题默认保持亮黄醒目

    title_font = PIL.ImageFont.truetype(font_path, title_size)
    content_font = PIL.ImageFont.truetype(font_path, content_size)

    all_lines_to_draw = []
    
    # 1. 标题处理
    if title.strip():
        curr_t = ""
        for char in list(title):
            test_t = curr_t + char
            if draw.textbbox((0, 0), test_t, font=title_font)[2] <= max_w:
                curr_t = test_t
            else:
                all_lines_to_draw.append((curr_t, title_font, title_color))
                curr_t = char
        all_lines_to_draw.append((curr_t, title_font, title_color))
        all_lines_to_draw.append(("", None, 30)) 

    # 2. 正文处理
    paragraphs = content.split('\n')
    for para in paragraphs:
        if not para.strip():
            all_lines_to_draw.append(("", None, 15))
            continue
        curr_c = ""
        for char in list(para):
            test_c = curr_c + char
            if draw.textbbox((0, 0), test_c, font=content_font)[2] <= max_w:
                curr_c = test_c
            else:
                all_lines_to_draw.append((curr_c, content_font, selected_color))
                curr_c = char
        all_lines_to_draw.append((curr_c, content_font, selected_color))

    # 3. 计算高度
    total_h = 0
    draw_data = []
    for line_text, font, info in all_lines_to_draw:
        if font is None:
            total_h += info
            draw_data.append((None, None, info))
        else:
            bbox = draw.textbbox((0, 0), line_text if line_text else " ", font=font)
            h = bbox[3] - bbox[1]
            w = bbox[2] - bbox[0]
            draw_data.append((line_text, font, info, w, h))
            total_h += h + line_spacing

    # 4. 绘制
    res_img = PIL.Image.new('RGBA', (720, total_h + 150), (255, 255, 255, 0))
    res_draw = PIL.ImageDraw.Draw(res_img)
    
    curr_y = 40
    for data in draw_data:
        if data[0] is None:
            curr_y += data[2]
        else:
            text, font, color, w, h = data
            x = (720 - w) // 2
            # 描边（增加文字厚度感）
            for off in [(-2,-2), (2,-2), (-2,2), (2,2)]:
                res_draw.text((x+off[0], curr_y+off[1]), text, font=font, fill=(0,0,0,200))
            res_draw.text((x, curr_y), text, font=font, fill=color)
            curr_y += h + line_spacing

    return np.array(res_img)

# --- [4. 视频合成] ---
def make_video_one_image(title_text, content_text, image_source, client, settings):
    paragraphs = [p.strip() for p in content_text.split('\n') if p.strip()]
    clips, temp_files = [], []
    
    for i, p in enumerate(paragraphs):
        res = client.synthesis(p, 'zh', 1, {
            "vol": 5, "spd": 5, "pit": 5, "per": settings['voice_id']
        })
        if isinstance(res, dict): continue
        tmp = f"v_{i}.mp3"
        with open(tmp, "wb") as f: f.write(res)
        temp_files.append(tmp)
        c = AudioFileClip(tmp)
        clips.append(c)
        clips.append(c.subclip(0, min(0.5, c.duration)).volumex(0)) # 短停顿
    
    if not clips: return None
    
    final_audio = concatenate_audioclips(clips)
    duration = final_audio.duration + 0.5
    temp_audio_path = "final_audio.mp3"
    final_audio.write_audiofile(temp_audio_path, fps=44100, logger=None)
    
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    try:
        # 背景
        if image_source:
            with PIL.Image.open(image_source) as img:
                img = img.convert("RGB")
                ratio = max(720/img.size[0], 1280/img.size[1])
                new_size = (int(img.size[0]*ratio), int(img.size[1]*ratio))
                bg_clip = ImageClip(np.array(img.resize(new_size, RESAMPLE_MODE))).set_duration(duration).set_position("center")
        else:
            bg_clip = ImageClip(np.zeros((1280, 720, 3))).set_duration(duration)

        # 文字
        full_text_arr = create_full_content_image(title_text, content_text, font_path, settings)
        text_clip = ImageClip(full_text_arr).set_duration(duration).set_position(('center', 100))

        final_video = CompositeVideoClip([bg_clip, text_clip], size=(720, 1280)).set_audio(AudioFileClip(temp_audio_path))
        output_name = "output_video.mp4"
        final_video.write_videofile(output_name, fps=24, codec="libx264", audio_codec="aac", logger=None)
        return output_name
    finally:
        if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
        for f in temp_files:
            if os.path.exists(f): os.remove(f)

# --- [5. 界面] ---
def main():
    st.set_page_config(page_title="短视频助手", layout="centered")
    if check_password():
        st.title("🎬 视频自动排版生成器")
        
        # --- 侧边栏配置 ---
        st.sidebar.header("⚙️ 样式设置")
        v_choice = st.sidebar.selectbox("🎙️ 选择音色", ["男声 (4179)", "女声 (4146)"], index=0)
        voice_id = 4179 if "男声" in v_choice else 4146
        
        t_color = st.sidebar.selectbox("🎨 文字颜色", ["鹅黄色", "科技白", "象牙金", "护眼绿", "天空蓝", "烈焰红"], index=0)
        
        t_size = st.sidebar.slider("📏 标题大小", 30, 80, 50)
        c_size = st.sidebar.slider("📝 正文大小", 20, 60, 32)
        
        settings = {
            "voice_id": voice_id,
            "text_color": t_color,
            "title_size": t_size,
            "content_size": c_size
        }

        # --- 主界面 ---
        client = AipSpeech(
            str(st.secrets["baidu_api"]["app_id"]), 
            str(st.secrets["baidu_api"]["api_key"]), 
            str(st.secrets["baidu_api"]["secret_key"])
        )
        
        u_title = st.text_input("💎 视频标题:", "在这里输入标题")
        u_content = st.text_area("✍️ 朗读内容:", height=300, placeholder="在此输入正文内容...")
        u_bg = st.file_uploader("📸 上传背景图:", type=["jpg", "png", "jpeg"])

        if st.button("🚀 开始合成视频"):
            if not u_content.strip():
                st.warning("内容不能为空")
                return
                
            src = u_bg if u_bg else ("default_bg.jpg" if os.path.exists("default_bg.jpg") else None)
            
            with st.spinner("视频渲染中... 请稍候"):
                try:
                    res = make_video_one_image(u_title, u_content, src, client, settings)
                    if res:
                        st.success("✨ 视频生成成功！")
                        st.video(res)
                        with open(res, "rb") as f:
                            st.download_button("📥 点击下载视频", f, "video_output.mp4")
                except Exception as e:
                    st.error(f"发生错误: {e}")

if __name__ == "__main__":
    main()
