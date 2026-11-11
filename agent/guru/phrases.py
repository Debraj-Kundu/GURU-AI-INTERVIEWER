"""Fixed lines the agent speaks with session.say (no LLM), or passes as one-off instructions."""

# The disclosure must be heard in full and worded the same every time, so no LLM writes it.
CONSENT_ASK = (
    "Hi, I'm Guru, an AI interviewer for {company_name}. This interview is recorded and transcribed "
    "for the recruiter's review. Do you agree to continue?"
)
GOODBYE = "We've reached the end of our time. Thank you for speaking with me today. Goodbye!"
CLOSING_GOODBYE = "Thank you for your time today. The recruiter will be in touch about next steps. Goodbye!"
DECLINED = "No problem at all. Thank you for your time, and goodbye!"
STILL_THERE = "Are you still there?"
LOOKUP_FILLER = "One moment, let me check that."
SILENCE_GOODBYE = "Seems like there's a technical issue, try again later."
# Start of the interview, right after consent: the format, then the first planned question as written.
INTRO = "Thank you. We'll go through {questions}, which should take about {minutes} minutes. Let's begin. {question}"
# ClosingAgent's opening line, one per way the interview ended (keys match closing.ENDINGS).
CLOSING_OPENERS = {
    "completed": "That's all the questions I have. Thank you for your answers. "
    "Do you have any questions about the company or the role?",
    "time_up": "We're out of time for questions, so let's stop there. Thank you. "
    "Do you have any questions about the company or the role?",
    "candidate_ended": "Of course, we can stop here. Thank you for your time. "
    "Before you go, do you have any questions about the company or the role?",
}
# Company question with nothing to answer from (NO_ANSWER / OUT_OF_SCOPE). The lookup tool says it.
NO_INFO = "That's a great question, but I don't have that information. The recruiter will be happy to answer it."
NO_INFO_CLOSING = NO_INFO + " Do you have any other questions?"
BACK_TO_QUESTION = NO_INFO + " Coming back to my question: {last}"
# The candidate rejoined after a dropped connection.
REJOINED = "Welcome back. As I was saying: {last}"
REJOINED_CLOSING = "Welcome back. Do you have any other questions about the company or the role?"
