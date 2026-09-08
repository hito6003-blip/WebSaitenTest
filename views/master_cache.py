import streamlit as st


MASTER_CACHE_TTL_SECONDS = 300


@st.cache_data(ttl=MASTER_CACHE_TTL_SECONDS, show_spinner=False)
def get_grader_master(_supabase):
    response = _supabase.table("graders").select("grader_id, grader_name, role_id, group_id").execute()
    return response.data or []


@st.cache_data(ttl=MASTER_CACHE_TTL_SECONDS, show_spinner=False)
def get_question_master(_supabase):
    response = _supabase.table("mst_questions").select(
        "response_id, question_title, correct_image_file_name"
    ).execute()
    return response.data or []


@st.cache_data(ttl=MASTER_CACHE_TTL_SECONDS, show_spinner=False)
def get_scoring_group_master(_supabase):
    response = _supabase.table("scoring_groups").select("group_id, group_name").execute()
    return response.data or []


def clear_master_cache():
    get_grader_master.clear()
    get_question_master.clear()
    get_scoring_group_master.clear()