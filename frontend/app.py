import streamlit as st
import requests
import os
import time

# Set page config for a premium wide layout
st.set_page_config(
    page_title="Vocab AI",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Backend service address
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Custom CSS for glassmorphism and premium aesthetics
st.markdown("""
<style>
    /* Global Typography */
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@400;500;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Outfit', sans-serif;
        color: #1E293B;
    }
    
    /* Main Background Pattern */
    .stApp {
        background-color: #F8FAFC;
        background-image: radial-gradient(#E2E8F0 1px, transparent 1px);
        background-size: 20px 20px;
    }
    
    /* Glassmorphism Cards */
    .glass-card {
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.5);
        border-radius: 16px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05), 0 8px 10px -6px rgba(0, 0, 0, 0.01);
        padding: 2rem;
        margin-bottom: 1.5rem;
        transition: transform 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
    }
    .glass-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
    }
    
    /* Word Title */
    .vocab-word {
        font-family: 'Outfit', sans-serif;
        font-size: 1.75rem;
        font-weight: 800;
        color: #0F172A;
        margin-top: 0;
        margin-bottom: 0.2rem;
        letter-spacing: -0.02em;
    }
    
    /* Meaning Badge */
    .meaning-box {
        background: linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%);
        border-left: 4px solid #3B82F6;
        padding: 1rem 1.25rem;
        border-radius: 0 8px 8px 0;
        margin-top: 1rem;
        color: #1E3A8A;
        font-size: 1.05rem;
        font-weight: 500;
        line-height: 1.5;
    }
    
    /* Small Tag */
    .pos-tag {
        background-color: #F1F5F9;
        color: #64748B;
        font-size: 0.8rem;
        padding: 0.2rem 0.6rem;
        border-radius: 9999px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-left: 10px;
        vertical-align: middle;
    }
    
    /* Custom Header */
    .app-header {
        text-align: center;
        padding: 3rem 0 2rem 0;
    }
    .app-title {
        font-size: 3.5rem;
        font-weight: 800;
        background: -webkit-linear-gradient(45deg, #3B82F6, #8B5CF6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
        line-height: 1.2;
    }
    .app-subtitle {
        color: #64748B;
        font-size: 1.2rem;
        margin-top: 0.5rem;
    }

    /* Tabs Styling override */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
        justify-content: center;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 1rem 2rem;
        background: transparent;
        border-radius: 8px 8px 0 0;
        font-size: 1.2rem;
        font-weight: 600;
        font-family: 'Outfit', sans-serif;
    }
    
    /* Hide Streamlit elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# Header
st.markdown("""
<div class='app-header'>
    <h1 class='app-title'>Vocab AI Studio ✨</h1>
    <p class='app-subtitle'>Effortlessly extract and master vocabulary from any book page.</p>
</div>
""", unsafe_allow_html=True)

# Main Navigation using Tabs
tab1, tab2 = st.tabs(["📤 Upload Image", "📖 Vocabulary Library"])

# ==========================================
# TAB 1: UPLOAD
# ==========================================
with tab1:
    st.markdown("<br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("""
        <div style='text-align: center; margin-bottom: 2rem;'>
            <h3 style='color: #334155;'>Process New Pages</h3>
            <p style='color: #64748B;'>Upload image pages of a book to instantly extract the vocabulary words and true API meanings.</p>
        </div>
        """, unsafe_allow_html=True)
        
        uploaded_file = st.file_uploader("Upload Image Page (PNG, JPG)", type=["png", "jpg", "jpeg"])
        
        if uploaded_file:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✨ Extract Vocabulary", use_container_width=True, type="primary"):
                
                # Setup UI states
                progress_bar = st.progress(0)
                status_text = st.empty()
                status_text.info("Uploading image to backend...")
                
                try:
                    # Execute API Request
                    files_payload = [("files", (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type))]
                    response = requests.post(f"{BACKEND_URL}/books/upload", files=files_payload, data={"title": uploaded_file.name})
                    
                    if response.status_code == 200:
                        book_id = response.json().get("book_id")
                        status_text.warning(f"Image sent! MinerU extraction is running in the background. (ID: {book_id})")
                        progress_bar.progress(30)
                        
                        # Poll for completion
                        for i in range(1, 60): # wait up to 3 mins
                            time.sleep(3)
                            # Advance progress bar slightly to show it's alive
                            progress = min(30 + i, 95)
                            progress_bar.progress(progress)
                            
                            res = requests.get(f"{BACKEND_URL}/books/{book_id}/status")
                            if res.status_code == 200:
                                status = res.json().get("status")
                                if status == "done":
                                    progress_bar.progress(100)
                                    status_text.success("🎉 Extraction fully complete! Check the Vocabulary Library tab.")
                                    st.balloons()
                                    break
                                elif status == "failed":
                                    progress_bar.progress(100)
                                    status_text.error(f"❌ Extraction failed: {res.json().get('error_message')}")
                                    break
                            else:
                                pass # ignore bad poll and try again
                        else:
                            status_text.error("Timeout waiting for extraction.")
                    else:
                        status_text.error(f"Upload failed: {response.text}")
                except Exception as e:
                    status_text.error(f"Backend Server Error: {e}")

# ==========================================
# TAB 2: VOCABULARY LIBRARY
# ==========================================
with tab2:
    st.markdown("<br>", unsafe_allow_html=True)
    
    search_col, stat_col = st.columns([3, 1])
    with search_col:
        search_query = st.text_input("🔍 Search your vocabulary library...", placeholder="Type a word...")
    
    request_params = {"page": 1, "page_size": 100} # Get up to 100 for display
    if search_query:
        request_params["search"] = search_query
        
    try:
        response = requests.get(f"{BACKEND_URL}/words", params=request_params)
        if response.status_code == 200:
            data = response.json()
            words = data["words"]
            
            with stat_col:
                st.markdown(f"<div style='text-align: right; margin-top: 35px; color: #64748B; font-weight: 500;'>Total Words: {data['total_count']}</div>", unsafe_allow_html=True)
                
            st.markdown("<hr style='opacity: 0.5;'>", unsafe_allow_html=True)
            
            if not words:
                st.info("No words found. Go to the Upload tab to extract some pages!")
            else:
                # Beautiful masonry-like column layout (2 columns)
                col1, col2 = st.columns(2)
                for i, word in enumerate(words):
                    lemma = word['lemma'].capitalize()
                    pos = word['part_of_speech'] or ""
                    
                    # Extract string meaning from definition_quiz dict or placeholder
                    meaning = "No meaning registered yet."
                    def_quiz = word.get('definition_quiz')
                    if def_quiz and type(def_quiz) == dict:
                        # Grab the first available value from the JSON
                        meaning = list(def_quiz.values())[0]

                    html_card = f'''
                    <div class="glass-card">
                        <div class="vocab-word">{lemma} <span class="pos-tag">{pos}</span></div>
                        <div class="meaning-box">{meaning}</div>
                    </div>
                    '''
                    
                    if i % 2 == 0:
                        col1.markdown(html_card, unsafe_allow_html=True)
                    else:
                        col2.markdown(html_card, unsafe_allow_html=True)
        else:
            st.error(f"Failed to fetch words: {response.text}")
    except Exception as e:
        st.error(f"Cannot connect to the backend database: {e}")
