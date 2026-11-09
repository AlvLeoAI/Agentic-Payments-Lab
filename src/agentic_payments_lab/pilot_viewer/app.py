"""Static historical view. All user-facing dynamic text comes from the strict snapshot."""

from .snapshot import SnapshotUnavailable, load_snapshot, usd


def main():
    import streamlit as st

    st.set_page_config(page_title="Pilot results · Agentic Payments Lab", layout="wide")
    try:
        data = load_snapshot()
    except SnapshotUnavailable:
        st.error("Saved pilot data is unavailable or invalid. No results are displayed.")
        return

    request_count = sum(len(s.requests) for s in data.scenarios)
    charge_count = data.scenarios[-1].authority_after.charges
    with st.container(gap="xsmall"):
        st.header(
            f"{len(data.scenarios)} scenarios passed · {charge_count} simulated charge · "
            f"{request_count} model requests",
            anchor=False,
        )
        st.caption(
            "Historical results · Live model · Simulated payments — "
            "Agentic Payments Lab / Phase 3B.8 / Recorded 8 November 2026"
        )

        # The summary is outside the tabs, so changing a detail tab never hides a scenario.
        with st.container(key="scenario-summary", gap="xsmall"):
            cards = st.columns(3, gap="small", border=True)
            for index, (card, scenario) in enumerate(zip(cards, data.scenarios, strict=True)):
                with card, st.container(gap="xxsmall"):
                    st.markdown("**" + scenario.name.title() + "**")
                    st.markdown(
                        "Simulated payment: **" + scenario.payment_state.replace("_", " ") + "**"
                    )
                    st.text("Delivery: " + scenario.delivery_state.replace("_", " "))
                    st.caption(
                        f"New charges: {data.charge_delta(index)} · "
                        f"Requests: {len(scenario.requests)}"
                    )
        st.caption("One cumulative simulated charge; replay and refusal add no charge.")

        cost, uncertainty = st.columns(2, gap="small")
        cost.metric("Fresh estimated API cost", usd(data.fresh_estimated_cost_nano_usd))
        cost.caption("Fresh completed attempt · usage estimate · billing unverified")
        uncertainty.metric(
            "Original unresolved reservation",
            usd(data.original_stopped_attempt.unresolved_reservation_nano_usd),
        )
        uncertainty.caption("Earlier stopped request · unchanged and unreleased")
        st.warning(
            "Earlier request: usage incomplete; reservation unchanged. "
            "API estimates and reservations are not billed totals."
        )
        with st.expander("Earlier stopped request: partial evidence"):
            stopped = data.original_stopped_attempt
            st.text(
                f"One request; {stopped.observed_input_tokens} observed input tokens and "
                f"{stopped.observed_output_tokens} observed output tokens."
            )
            st.text("Usage accounting: incomplete. Accepted cost estimate: unknown.")
            st.caption(
                "The original history and reservation remain unchanged; nothing was repriced."
            )

        tabs = st.tabs(["Purchase", "Replay", "Refusal"])
        for index, (tab, scenario) in enumerate(zip(tabs, data.scenarios, strict=True)):
            with tab, st.container(gap="xsmall"):
                st.subheader(
                    "Simulated payment: " + scenario.payment_state.replace("_", " "),
                    anchor=False,
                )
                description = {
                    "purchase": "A report acquired",
                    "replay": "The same purchase, inspected again; no new charge",
                    "refusal": "An unaffordable request, declined; no purchase created",
                }[scenario.name]
                st.caption(
                    description + " · Delivery: " + scenario.delivery_state.replace("_", " ")
                )
                metrics = st.columns(4, gap="small")
                metrics[0].metric("Requests", str(len(scenario.requests)))
                metrics[1].metric("Input tokens", f"{scenario.usage.input_tokens:,}")
                metrics[2].metric("Output tokens", str(scenario.usage.output_tokens))
                metrics[3].metric("Estimated API cost", usd(scenario.estimated_cost_nano_usd))
                with st.expander("Payment & tool evidence"):
                    st.text("Agent termination: " + scenario.termination)
                    st.text("Recorded decision: " + scenario.decision)
                    st.text("Payment state: " + scenario.payment_state)
                    st.text("Delivery state: " + scenario.delivery_state)
                    if scenario.purchase_alias is None:
                        st.text("Purchase: none created. The refusal task has no purchase row.")
                    else:
                        st.text("Purchase alias: " + scenario.purchase_alias)
                        st.text("Purchase amount: 10000 atomic units / 0.010000 SIM-USDC")
                    st.text(
                        f"New simulated charges in this scenario: {data.charge_delta(index)} · "
                        "Cumulative charges after this scenario: "
                        f"{scenario.authority_after.charges}"
                    )
                    st.caption("Cumulative authority snapshots are not added together.")
                    st.text(
                        f"Task grant spent: {scenario.grant_spent_atomic} atomic · "
                        f"reserved: {scenario.grant_reserved_atomic} atomic"
                    )
                    st.markdown("**Recorded tool outcomes**")
                    for event in scenario.tool_trace:
                        st.text(f"{event.attempt}. {event.tool} → {event.outcome}")
                    st.caption(
                        "Tool outcomes are recorded host events, not model-generated success prose."
                    )
                with st.expander("Request usage evidence"):
                    for request in scenario.requests:
                        st.text(
                            f"Request {request.ordinal}: {request.usage.input_tokens} input + "
                            f"{request.usage.output_tokens} output = "
                            f"{request.usage.total_tokens} tokens; "
                            f"estimate {usd(request.estimated_cost_nano_usd)}"
                        )
                    st.text("Cache-write aggregate: 0. Lifetime-details object: absent.")
                    st.text(
                        "Observed 30-minute / 12-hour allocations: unknown (null) / unknown (null)."
                    )
                    st.text(
                        "Separately derived allocations: 0 / 0, from the explicit zero aggregate."
                    )
                    st.caption(
                        "Observed fields remain unknown; derived zeros are an accounting inference."
                    )

        st.markdown("**Fresh run totals**")
        st.text(
            f"{request_count} requests · {data.fresh_usage.input_tokens:,} input + "
            f"{data.fresh_usage.output_tokens} output = {data.fresh_usage.total_tokens:,} tokens."
        )
        st.caption(
            "SIM-USDC has no monetary value. These simulator records establish no blockchain "
            "settlement or general model reliability, and authorize no further runs."
        )
        with st.expander("Historical source and scope"):
            st.text(
                "Requested / returned model: gpt-6-luna · tier: default · "
                "reasoning: none · retries: 0"
            )
            st.text("Snapshot schema: 1 · source: sanitized saved pilot reports")
            st.text("Tested base commit: " + data.provenance.tested_base_commit)
            st.text("Tested source manifest SHA256: " + data.provenance.tested_manifest_sha256)
            st.text("Later publication commit: " + data.provenance.published_commit)
            st.caption(
                "The pilot ran against reviewed uncommitted source. Publication later included "
                "documentation closure; it is not the identity of the earlier tested working tree. "
                "This viewer reads only its bundled snapshot, never the original databases."
            )
