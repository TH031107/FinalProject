"""현재 세션의 자료가 바뀌면 분석과 이후 대화 상태를 함께 무효화한다."""
def reset_analysis(state):
    for key in ('analysis', 'messages', 'agent_state', 'active_student', 'image_cache', 'chat_diagnostics'):
        state.pop(key, None)


def synchronize(state, input_key):
    if state.get('input_key') != input_key:
        reset_analysis(state)
    state['input_key'] = input_key
