"""Smart applier — reads any application form and answers it from verified facts.

Pipeline for one application:

    scan_form(page)            -> tuple[Question, ...]          (questions.py)
    resolve(question, cand)    -> Resolution | None             (resolve.py)
    draft_answers(...)         -> {qid: Draft}  LLM, verified    (drafting.py)
    decide + fill + submit                                      (applier.py)

Autonomy rule: the form is submitted only when every REQUIRED question has an
answer that traces back to the candidate's own facts. Anything else is held,
logged to pending_questions.json for the user to answer once, and retried on a
later run. Demographic, consent, and credential questions are never guessed.
"""
