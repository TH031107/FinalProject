"""검증된 계획의 로컬 도구 결과를 문장화한다. 모델/네트워크 호출 없음."""

def lookup(task, result, reverse):
    pieces=[]
    r=result
    if task.action=='statistics':
        pieces.append(f'학생 {len(r.students)}명, 만점 {r.maximum}점입니다. 평균 {r.average:.2f}점, 중앙값 {r.median:.2f}점, 모집단 표준편차 {r.population_stddev:.2f}입니다. 오답 상위 문항: '+(', '.join(map(str,r.top_wrong)) or '없음'))
    elif task.action=='weakest':
        pieces.append('최저 점수 학생: '+', '.join(f'{s.identifier} ({s.score}점)' for s in r.weakest())+'입니다. 동점자는 모두 표시했습니다.')
    elif task.action in {'student','rank_goal'}:
        if task.student_alias not in reverse:
            pieces.append('해당 학생을 확인할 수 없습니다. 업로드된 학생 식별자를 정확히 알려 주세요.')
        else:
            s=r.student(reverse[task.student_alias])
            if task.action=='student':
                pieces.append(f'{s.identifier}: {s.score}점, {s.rank}위, 정답 {sum(s.correct)}개, 미응답 {sum(s.unanswered)}개입니다.')
            elif task.target_rank is None or not 1 <= task.target_rank <= len(r.students):
                pieces.append(f'목표 등수를 1~{len(r.students)} 사이로 알려 주세요.')
            else:
                goal=r.rank_goal(s.identifier,task.target_rank)
                if goal['already']:
                    pieces.append(f'{s.identifier}은 현재 {s.score}점, {s.rank}위로 이미 목표 {task.target_rank}위 이내입니다.')
                else:
                    relative = (f'기존 정답 수 대비 증가율은 {goal["relative_increase"]:.2f}%입니다. '
                                if goal['relative_increase'] is not None else '현재 정답 수가0개이므로 상대 증가율은 계산하지 않습니다. ')
                    pieces.append(f'{s.identifier}은 현재 {s.score}점, {s.rank}위, 정답률 {goal["correct_rate"]:.2f}%입니다. '
                        f'다른 학생 점수가 그대로이고 공동 순위를 허용하면 {task.target_rank}위 이내 기준은 {goal["threshold"]}점으로, '
                        f'{goal["needed_points"]}점 이상 더 필요합니다. 오답·미응답 중 고배점 문항부터 맞히면 최소 {goal["extra_count"]}문항, '
                        f'전체 문항 대비 정답률 약 {goal["percentage_points"]:.2f}%포인트 상승이 필요합니다. '
                        f'그 조합의 실제 추가 배점은 {goal["gain"]}점, 예상 점수는 {goal["new_score"]}점입니다. '+relative+
                        '문항별 배점이 달라 어떤 문항을 맞히는지에 따라 필요한 개수는 늘어날 수 있습니다. 단독 순위는 별도 조건입니다.')
    elif task.action=='greeting':
        pieces.append('안녕하세요. 평균·중앙값 등 성적 통계, 학생별 점수, 최저 점수 학생 조회와 유사 문제 생성을 도와드립니다.')
    elif task.action=='refuse':
        pieces.append('내부 설정·도구·비밀 정보는 공개할 수 없습니다.')
    elif task.action=='unknown':
        pieces.append('업로드된 시험 자료에는 해당 정보가 없어 확인할 수 없습니다. 출결 등 미제공 정보는 추정하지 않습니다.')
    return pieces[0]
