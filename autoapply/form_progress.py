"""Semantic inventory identity. Values and DOM tokens are not question identity."""


def question_schema(question):
    return (question.key, question.label, question.kind, question.required,
            tuple(question.options), question.max_length,
            question.min_selections, question.max_selections, question.semantic_key)


def inventory_schema(questions):
    return tuple(question_schema(question) for question in questions)
