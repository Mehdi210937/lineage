"""Page Coach : conseils de production d'un LLM local, fondés uniquement sur le graphe et les bios."""
from __future__ import annotations

import httpx
import streamlit as st

import ui
from lineage.config import get_settings
from lineage.explore.coach import ask_coach, build_facts, build_messages, ollama_status, validate_answer

settings = get_settings()


@st.cache_data(ttl=30, show_spinner=False)
def status(url: str, model: str) -> tuple[bool, str]:
    with httpx.Client() as client:
        return ollama_status(client, url, model)


@st.cache_data(show_spinner=False)
def facts_for(path: str, version: float, mbids: tuple):
    return build_facts(path, list(mbids))


st.title("Coach de production")
st.write("Choisis tes inspirations : le coach donne des conseils en s'appuyant **uniquement** sur les "
         "données du graphe et les bios, et cite la source de chaque conseil.")

ok, msg = status(settings.ollama_url, settings.ollama_model)
(st.success if ok else st.warning)(msg, icon=":material/smart_toy:")

mbids, labels = ui.artist_options()
picked = st.multiselect("Inspirations", mbids, format_func=labels.get, max_selections=5, key="coach_artists",
                        placeholder="Cherche un artiste…")
goal = st.text_area("Ce que tu veux produire (facultatif)",
                    placeholder="Ex. : un morceau lent, voix féminine noyée, ambiance froide")

if not picked:
    st.info("Choisis au moins une inspiration.")
    st.stop()

facts = facts_for(str(ui.db_path()), ui.db_version(), tuple(picked))
by_id = {f.id: f for f in facts}
with st.expander(f"Faits transmis au coach ({len(facts)})"):
    st.caption("C'est tout ce que le LLM reçoit : il n'a pas le droit d'utiliser autre chose.")
    for f in facts:
        st.markdown(f"**{f.id}** · *{f.source}* — {f.text}"
                    + (f"  \n> {f.citation}" if f.citation else ""))

if not facts:
    st.warning("Aucune donnée sur ces artistes : impossible de donner des conseils sourcés.")
    st.stop()

if st.button("Demander conseil", type="primary", disabled=not ok):
    messages = build_messages(facts, [labels[m] for m in picked], goal)
    with st.spinner(f"{settings.ollama_model} réfléchit… (une minute ou deux sur un PC sans GPU)"):
        try:
            with httpx.Client() as client:
                answer = ask_coach(client, settings.ollama_url, settings.ollama_model, messages)
        except (httpx.HTTPError, ValueError) as exc:
            st.error(f"Le coach n'a pas pu répondre : {exc}")
            st.stop()
    st.session_state["coach_result"] = (tuple(picked), goal, answer)

result = st.session_state.get("coach_result")
if result and result[0] == tuple(picked) and result[1] == goal:
    answer = result[2]
    kept, rejected = validate_answer(answer, facts)
    st.subheader("Conseils")
    if not kept:
        st.info("Le coach n'a donné aucun conseil justifié par les données.")
    for i, a in enumerate(kept, 1):
        with st.container(border=True):
            st.markdown(f"**{i}.** {a.conseil}")
            for sid in a.sources:
                f = by_id[sid]
                text = f.text if len(f.text) <= 220 else f.text[:220].rsplit(" ", 1)[0].rstrip(" .,;:") + "…"
                line = f"`{sid}` *{f.source}* — {text}"
                if f.citation:
                    line += f" — « {f.citation} »"
                st.caption(line)
    if answer.limites:
        st.markdown(f"**Ce que les données ne disent pas :** {answer.limites}")
    if rejected:
        with st.expander(f"{len(rejected)} conseil(s) écarté(s) faute de source valide"):
            for a in rejected:
                st.markdown(f"- {a.conseil} (sources citées : {', '.join(a.sources) or 'aucune'})")
