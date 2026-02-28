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

# --- [2. 初始化 Session State] ---
# 用来记住生成的临时文件路径，防止点击下载后消失
if "v_path" not in st.session_state: st.session_state.v_path = None
if "a_path" not in st.session_state: st.session_state.a_path = None
if "pure_a_path" not in st.session_state: st.session_state.pure_a_path = None

# --- [3. 图像处理补丁] ---
if hasattr(PIL.Image, 'Resampling'):
    RESAMPLE_MODE = PIL.Image.Resampling.LANCZOS
else:
    RESAMPLE_MODE = getattr(PIL.Image, 'ANTIALIAS', PIL.Image.BICUBIC)

# --- [4. 高清渲染逻辑 (保持不变)] ---
def create_hd_text_image(title, content, font_path, settings):
    scale = 2  
    base_w = 720
    canvas_w = base_w * scale
    max_text_w = (base_w - 100) * scale 
    color_map = {"鹅黄色": (255, 241, 67), "科技白": (245, 245, 245), "象牙金": (250, 218, 141), "护眼绿": (199, 237, 204), "天空蓝": (135, 206, 235)}
    text_color = color_map.get(settings['text_color'], (255, 241, 67))
    title_font = PIL.ImageFont.truetype(font_path, settings['title_size'] * scale)
    content_font = PIL.ImageFont.truetype(font_path, settings['content_size'] * scale)
    line_spacing = 12 * scale
    temp_draw = PIL.ImageDraw.Draw(PIL.Image.new('RGBA', (canvas_w, 10), (0,0,0,0)))
    all_lines = []
    if title.strip():
        curr = ""
        for char in title:
            test = curr + char
            if temp_draw.textbbox((0, 0), test, font=title_font)[2] <= max_text_w: curr = test
            else: all_lines.append((curr, title_font, (255,255,0))); curr = char
        all_lines.append((curr, title_font, (255,255,0)))
        all_lines.append(("", None, 40 * scale)) 
    for para in content.split('\n'):
        if not para.strip(): all_lines.append(("", None, 20 * scale)); continue
        curr = ""
        for char in para:
            test = curr + char
            if temp_draw.textbbox((0, 0), test, font=content_font)[2] <= max_text_w: curr = test
            else: all_lines.append((curr, content_font, text_color)); curr = char
        all_lines.append((curr, content_font, text_color))
    total_h = 0
    draw_plan = []
    for line_text, font, info in all_lines:
        if font is None: total_h += info; draw_plan.append((None, None, info))
        else:
            bbox = temp_draw.textbbox((0, 0), line_text if line_text else " ", font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw_plan.append((line_text, font, info, w, h)); total_h += h + line_spacing
    hd_img = PIL.Image.new('RGBA', (canvas_w, total_h + 200), (0, 0, 0, 0))
    hd_draw = PIL.ImageDraw.Draw(hd_img)
    curr_y = 100
    for item in draw_plan:
        if item[0] is None: curr_y += item[2]
        else:
            txt, font, clr, w, h = item; x = (canvas_w - w) // 2
            for off in [(-3,-3), (3,-3), (-3,3), (3,3), (0,3), (0,-3)]: hd_draw.text((x+off[0], curr_y+off[1]), txt, font=font, fill=(0,0,0,255))
            hd_draw.text((x, curr_y), txt, font=font, fill=clr); curr_y += h + line_spacing
    final_h = int(hd_img.size[1] / scale)
    return np.array(hd_img.resize((base_w, final_h), RESAMPLE_MODE))

# --- [5. 核心处理函数] ---
def process_audio_only(content, client, voice_id):
    paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
    clips, temp_files = [], []
    for i, p in enumerate(paragraphs):
        res = client.synthesis(p, 'zh', 1, {"vol": 5, "spd": 5, "pit": 5, "per": voice_id})
        if isinstance(res, dict): continue
        tmp = f"audio_p_{i}.mp3"
        with open(tmp, "wb") as f: f.write(res)
        temp_files.append(tmp)
        c = AudioFileClip(tmp)
        clips.append(c); clips.append(c.subclip(0, min(0.4, c.duration)).volumex(0))
    if not clips: return None
    final_audio = concatenate_audioclips(clips)
    output = "pure_audio_output.mp3"
    final_audio.write_audiofile(output, fps=44100, logger=None)
    for f in temp_files:
        if os.path.exists(f): os.remove(f)
    return output

def process_video_full(title, content, bg_source, client, settings):
    paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
    clips, temp_files = [], []
    for i, p in enumerate(paragraphs):
        res = client.synthesis(p, 'zh', 1, {"vol": 5, "spd": 5, "pit": 5, "per": settings['voice_id']})
        if isinstance(res, dict): continue
        tmp = f"v_p_{i}.mp3"
        with open(tmp, "wb") as f: f.write(res)
        temp_files.append(tmp)
        c = AudioFileClip(tmp)
        clips.append(c); clips.append(c.subclip(0, min(0.4, c.duration)).volumex(0))
    if not clips: return None, None
    final_audio = concatenate_audioclips(clips)
    duration = final_audio.duration + 0.5
    a_out = "video_audio_track.mp3"
    final_audio.write_audiofile(a_out, fps=44100, logger=None)
    v_out = "hd_video_output.mp4"
    font_path = "simhei.ttf" if os.path.exists("simhei.ttf") else "Arial"
    try:
        if settings['bg_type'] == "上传图片" and bg_source:
            with PIL.Image.open(bg_source) as img:
                img = img.convert("RGB")
                r = max(720/img.size[0], 1280/img.size[1])
                new_size = (int(img.size[0]*r), int(img.size[1]*r))
                bg_arr = np.array(img.resize(new_size, RESAMPLE_MODE))
        else:
            colors = {"深邃黑": (15, 15, 15), "莫兰迪灰": (70, 80, 88), "奶茶色": (198, 172, 143), "暗夜绿": (18, 44, 40), "磨砂蓝": (36, 54, 75)}
            bg_arr = np.full((1280, 720, 3), colors.get(settings['bg_color'], (15, 15, 15)), dtype=np.uint8)
        bg_clip = ImageClip(bg_arr).set_duration(duration).set_position("center")
        text_arr = create_hd_text_image(title, content, font_path, settings)
        text_clip = ImageClip(text_arr).set_duration(duration).set_position(('center', 120))
        final_video = CompositeVideoClip([bg_clip, text_clip], size=(720, 1280)).set_audio(AudioFileClip(a_out))
        final_video.write_videofile(v_out, fps=24, codec="libx264", audio_codec="aac", bitrate="4500k", logger=None)
        return v_out, a_out
    finally:
        for f in temp_files:
            if os.path.exists(f): os.remove(f)

# --- [6. 界面入口] ---
def main():
    st.set_page_config(page_title="姜老师助手", layout="centered")
    if not check_password(): return

    st.title("🎬 姜老师朗读小助手")
    client = AipSpeech(str(st.secrets["baidu_api"]["app_id"]), str(st.secrets["baidu_api"]["api_key"]), str(st.secrets["baidu_api"]["secret_key"]))

    tab_video, tab_audio = st.tabs(["🎥 高清视频生成", "🎵 仅生成 MP3 配音"])

    # --- 视频 Tab ---
    with tab_video:
        with st.expander("⚙️ 视频样式设置", expanded=True):
            v_col1, v_col2 = st.columns(2)
            with v_col1:
                v_opt = st.selectbox("🎙️ 音色", ["男声 (4179)", "女声 (4146)"], key="v_v")
                v_id = 4179 if "男声" in v_opt else 4146
                t_size = st.slider("📏 标题", 30, 80, 52, key="v_t")
                bg_mode = st.radio("🖼️ 背景模式", ["上传图片", "纯色背景"], horizontal=True, key="v_bg_m")
            with v_col2:
                t_color = st.selectbox("🎨 文字颜色", ["鹅黄色", "科技白", "象牙金", "护眼绿", "天空蓝"], key="v_c")
                c_size = st.slider("📝 正文字号", 20, 60, 34, key="v_s")
                bg_color = st.selectbox("🎨 背景色", ["深邃黑", "莫兰迪灰", "奶茶色", "暗夜绿", "磨砂蓝"]) if bg_mode == "纯色背景" else "深邃黑"
            v_need_mp3 = st.checkbox("导出 MP3 文件", value=True, key="v_mp3")

        st.markdown("---")
        v_title = st.text_input("💎 标题:", "输入标题", key="v_title_in")
        v_content = st.text_area("✍️ 正文:", height=250, key="v_cont_in")
        v_bg_file = st.file_uploader("📸 图片:", type=["jpg", "png", "jpeg"]) if bg_mode == "上传图片" else None

        if st.button("🚀 开始合成高清视频", use_container_width=True):
            if not v_content.strip(): st.error("内容不能为空"); return
            # 清除旧记录
            st.session_state.v_path, st.session_state.a_path = None, None
            settings = {"voice_id": v_id, "text_color": t_color, "title_size": t_size, "content_size": c_size, "bg_type": bg_mode, "bg_color": bg_color}
            with st.spinner("视频渲染中..."):
                v_p, a_p = process_video_full(v_title, v_content, v_bg_file, client, settings)
                if v_p:
                    st.session_state.v_path = v_p
                    st.session_state.a_path = a_p

        # 这里是关键：只要笔记本里有路径，就一直显示按钮
        if st.session_state.v_path:
            st.success("✅ 渲染完成！")
            st.video(st.session_state.v_path)
            d_col1, d_col2 = st.columns(2)
            with d_col1:
                with open(st.session_state.v_path, "rb") as f:
                    st.download_button("📥 下载 MP4 视频", f, "video.mp4", use_container_width=True)
            if v_need_mp3 and st.session_state.a_path:
                with d_col2:
                    with open(st.session_state.a_path, "rb") as f:
                        st.download_button("🎵 下载 MP3 音频", f, "audio.mp3", use_container_width=True)

    # --- 音频 Tab ---
    with tab_audio:
        a_voice_opt = st.selectbox("🎙️ 音色", ["男声 (4179)", "女声 (4146)"], key="a_v")
        a_voice_id = 4179 if "男声" in a_voice_opt else 4146
        a_content = st.text_area("✍️ 朗读内容:", height=300, key="a_cont_in")
        
        if st.button("🎵 立即生成 MP3", use_container_width=True):
            st.session_state.pure_a_path = None # 清除旧记录
            if not a_content.strip(): st.error("请输入内容"); return
            with st.spinner("音频合成中..."):
                a_out = process_audio_only(a_content, client, a_voice_id)
                if a_out: st.session_state.pure_a_path = a_out

        if st.session_state.pure_a_path:
            st.success("✅ 音频生成成功！")
            st.audio(st.session_state.pure_a_path)
            with open(st.session_state.pure_a_path, "rb") as f:
                st.download_button("📥 下载 MP3 文件", f, "pure_audio.mp3", use_container_width=True)

if __name__ == "__main__":
    main()
