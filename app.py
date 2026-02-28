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

# --- [3. 高清超采样文字渲染逻辑] ---
def create_hd_text_image(title, content, font_path, settings):
    scale = 2  
    base_w = 720
    canvas_w = base_w * scale
    max_text_w = (base_w - 100) * scale 
    
    color_map = {
        "鹅黄色": (255, 241, 67),
        "科技白": (245, 245, 245),
        "象牙金": (250, 218, 141),
        "护眼绿": (199, 237, 204),
        "天空蓝": (135, 206, 235)
    }
    text_color = color_map.get(settings['text_color'], (255, 241, 67))
    title_color = (255, 255, 0)

    title_font = PIL.ImageFont.truetype(font_path, settings['title_size'] * scale)
    content_font = PIL.ImageFont.truetype(font_path, settings['content_size'] * scale)
    line_spacing = 12 * scale

    temp_draw = PIL.ImageDraw.Draw(PIL.Image.new('RGBA', (canvas_w, 10), (0,0,0,0)))
    all_lines = []
    
    if title.strip():
        curr = ""
        for char in title:
            test = curr + char
            if temp_draw.textbbox((0, 0), test, font=title_font)[2] <= max_text_w:
                curr = test
            else:
                all_lines.append((curr, title_font, title_color))
                curr = char
        all_lines.append((curr, title_font, title_color))
        all_lines.append(("", None, 40 * scale)) 

    for para in content.split('\n'):
        if not para.strip():
            all_lines.append(("", None, 20 * scale))
            continue
        curr = ""
        for char in para:
            test = curr + char
            if temp_draw.textbbox((0, 0), test, font=content_font)[2] <= max_text_w:
                curr = test
            else:
                all_lines.append((curr, content_font, text_color))
                curr = char
        all_lines.append((curr, content_font, text_color))

    total_h = 0
    draw_plan = []
    for line_text, font, info in all_lines:
        if font is None:
            total_h += info
            draw_plan.append((None, None, info))
        else:
            bbox = temp_draw.textbbox((0, 0), line_text if line_text else " ", font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw_plan.append((line_text, font, info, w, h))
            total_h += h + line_spacing

    hd_img = PIL.Image.new('RGBA', (canvas_w, total_h + 200), (0, 0, 0, 0))
    hd_draw = PIL.ImageDraw.Draw(hd_img)
    
    curr_y = 100
    for item in draw_plan:
        if item[0] is None:
            curr_y += item[2]
        else:
            txt, font, clr, w, h = item
            x = (canvas_w - w) // 2
            # 强化描边
            for off in [(-3,-3), (3,-3), (-3,3), (3,3), (0,3), (0,-3)]:
                hd_draw.text((x+off[0], curr_y+off[1]), txt, font=font, fill=(0,0,0,255))
            hd_draw.text((x, curr_y), txt, font=font, fill=clr)
            curr_y += h + line_spacing

    final_h = int(hd_img.size[1] / scale)
    return np.array(hd_img.resize((base_w, final_h), RESAMPLE_MODE))

# --- [4. 视频合成核心] ---
def build_video(title, content, bg_source, client, settings):
    paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
    clips, temp_files = [], []
    
    for i, p in enumerate(paragraphs):
        res = client.synthesis(p, 'zh', 1, {
            "vol": 5, "spd": 5, "pit": 5, "per": settings['voice_id']
        })
        if isinstance(res, dict): continue
        tmp = f"part_{i}.mp3"
        with open(tmp, "wb") as f: f.write(res)
        temp_files.append(tmp)
        c = AudioFileClip(tmp)
        clips.append(c)
        clips.append(c.subclip(0, min(0.4, c.duration)).volumex(0)) 
    
    if not clips: return None
    
    final_audio = concatenate_audioclips(clips)
    duration = final_audio.duration + 0.5
    temp_audio = "temp_final.mp3"
    final_audio.write_audiofile(temp_audio, fps=44100, logger=None)
    
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"

    try:
        # 背景处理逻辑
        if settings['bg_type'] == "上传图片" and bg_source:
            with PIL.Image.open(bg_source) as img:
                img = img.convert("RGB")
                r = max(720/img.size[0], 1280/img.size[1])
                new_size = (int(img.size[0]*r), int(img.size[1]*r))
                bg_arr = np.array(img.resize(new_size, RESAMPLE_MODE))
        else:
            # 纯色背景库 (RGB)
            pure_colors = {
                "深邃黑": (15, 15, 15),
                "莫兰迪灰": (70, 80, 88),
                "奶茶色": (198, 172, 143),
                "暗夜绿": (18, 44, 40),
                "磨砂蓝": (36, 54, 75)
            }
            c_rgb = pure_colors.get(settings['bg_color'], (15, 15, 15))
            bg_arr = np.full((1280, 720, 3), c_rgb, dtype=np.uint8)

        bg_clip = ImageClip(bg_arr).set_duration(duration).set_position("center")

        # 文字图层生成
        text_arr = create_hd_text_image(title, content, font_path, settings)
        text_clip = ImageClip(text_arr).set_duration(duration).set_position(('center', 120))

        final_video = CompositeVideoClip([bg_clip, text_clip], size=(720, 1280)).set_audio(AudioFileClip(temp_audio))
        
        output_name = "hd_video_output.mp4"
        final_video.write_videofile(
            output_name, 
            fps=24, 
            codec="libx264", 
            audio_codec="aac", 
            bitrate="4500k",
            logger=None
        )
        return output_name
    finally:
        if os.path.exists(temp_audio): os.remove(temp_audio)
        for f in temp_files:
            if os.path.exists(f): os.remove(f)

# --- [5. 界面入口] ---
def main():
    st.set_page_config(page_title="高清视频助手", layout="centered")
    if not check_password(): return

    st.title("🎬 视频自动渲染助手")
    
    # --- 配置面板 ---
    with st.expander("⚙️ 样式与配音设置", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            v_opt = st.selectbox("🎙️ 朗读音色", ["男声 (4179)", "女声 (4146)"])
            v_id = 4179 if "男声" in v_opt else 4146
            t_size = st.slider("📏 标题字号", 30, 80, 52)
            bg_mode = st.radio("🖼️ 背景模式", ["上传图片", "纯色背景"], horizontal=True)
            
        with col2:
            t_color = st.selectbox("🎨 文字颜色", ["鹅黄色", "科技白", "象牙金", "护眼绿", "天空蓝"])
            c_size = st.slider("📝 正文字号", 20, 60, 34)
            if bg_mode == "纯色背景":
                bg_color = st.selectbox("🎨 选择背景色", ["深邃黑", "莫兰迪灰", "奶茶色", "暗夜绿", "磨砂蓝"])
            else:
                bg_color = "深邃黑" # 默认

    settings = {
        "voice_id": v_id,
        "text_color": t_color,
        "title_size": t_size,
        "content_size": c_size,
        "bg_type": bg_mode,
        "bg_color": bg_color
    }

    # 百度 API 初始化
    client = AipSpeech(
        str(st.secrets["baidu_api"]["app_id"]), 
        str(st.secrets["baidu_api"]["api_key"]), 
        str(st.secrets["baidu_api"]["secret_key"])
    )

    # --- 输入区 ---
    st.markdown("---")
    u_title = st.text_input("💎 视频标题:", "输入你的标题")
    u_content = st.text_area("✍️ 朗读内容:", height=300, placeholder="在此输入需要生成的文字内容...")
    
    u_bg = None
    if bg_mode == "上传图片":
        u_bg = st.file_uploader("📸 上传背景图片:", type=["jpg", "png", "jpeg"])
    
    st.write("") 
    gen_btn = st.button("🚀 开始合成高清视频", use_container_width=True)

    if gen_btn:
        if not u_content.strip():
            st.error("请输入内容后再生成！")
            return
            
        with st.spinner("正在合成高清视频..."):
            try:
                video_path = build_video(u_title, u_content, u_bg, client, settings)
                if video_path:
                    st.success("✅ 渲染完成！")
                    st.video(video_path)
                    with open(video_path, "rb") as f:
                        st.download_button("📥 下载 MP4 视频", f, "output.mp4")
            except Exception as e:
                st.error(f"渲染出错: {e}")

if __name__ == "__main__":
    main()
