"""Explicitly selected collaboration strategies; none is a runtime default."""

from group.profiles import CollaborationProfile
from group.records import DispatchPlan, RunProposal
from group.scheduling import SchedulingDecision, SchedulingView
from group.tools import _TOOLS, group_broadcast


class OnDemandStrategy:
    """Only accepted response requests activate peers; public posts stay passive."""

    name = 'on_demand'
    version = '1'
    profile = CollaborationProfile(
        name='on-demand', tools=(*_TOOLS, group_broadcast), require_public_reply=True,
        instructions=(
            'Work on your assigned trigger_messages; background messages are context, not additional assignments. '
            'Reply to each trigger using group_post with its actual message ID as reply_to. '
            'A public post does not activate peers. Use group_request for selected peers '
            'or group_broadcast to request every other peer when their input is needed. '
            'Mentions, reply links, recipient labels, and requests written inside a group_post are only text or attribution; '
            'they never schedule a turn. To hand work back to a peer, call group_request with that member in recipients '
            'and describe the next task, even if you have already posted the result. '
            'An empty request recipient list asks one available peer to respond. '
            'Inspect group_members before requesting help if availability matters. '
            'Requests you send and their acceptance receipts do not add assignments to this execution. '
            'An acceptance receipt does not require an answer from its sender. '
            'You may contribute relevant public follow-ups; these remain passive posts. '
            'Do not request another response merely to acknowledge a reply. '
            'Use group_yield when your current work is done; final prose stays private.'
        ),
    )

    def decide(self, view: SchedulingView) -> SchedulingDecision:
        snapshot = view.snapshot
        idle = [member.id for member in snapshot.members if member.state == 'idle']
        capacity = min(view.queue_capacity, snapshot.max_runs - snapshot.admitted_count)
        runs = []
        for opportunity in view.pending:
            if not idle or len(runs) >= capacity:
                break
            if opportunity.target is not None and opportunity.target not in idle:
                continue
            member = opportunity.target if opportunity.target is not None else idle[0]
            idle.remove(member)
            runs.append(RunProposal(member, 'Respond to this explicit request using the selected communication profile.',
                                    (opportunity.id,)))
        observed = view.messages.next_cursor if view.messages.items else None
        if not runs and observed is None:
            return SchedulingDecision(reason='No eligible request for an available member')
        return SchedulingDecision(DispatchPlan(snapshot.invocation_id, snapshot.revision,
                                  runs=tuple(runs), observed_through=observed))
