"""ai: suhbat bosqichi va takroriy salomni olib tashlash."""
import ai


def U(t):
    return {"role": "user", "content": t}


def A(t):
    return {"role": "assistant", "content": t}


def test_stage_first_reply():
    ongoing, note = ai._stage([U("salom")])
    assert not ongoing and "birinchi javob" in note


def test_stage_ongoing():
    ongoing, note = ai._stage([U("salom"), A("Assalomu alaykum! Men Anna."), U("2*2 nechi?")])
    assert ongoing and "DAVOM" in note


def test_stage_owner_message_is_not_anna_reply():
    ongoing, _ = ai._stage([A(f"{ai.OWNER_WROTE_PREFIX} keyinroq yozaman"), U("xo'p")])
    assert not ongoing


def test_strip_repeat_greeting():
    h = [U("salom"), A("Assalomu alaykum! Men Anna."), U("2*2 nechi?")]
    assert ai._strip_repeat_greeting("Assalomu alaykum! 2*2 = 4.", h) == "2*2 = 4."
    assert ai._strip_repeat_greeting("Salom, Ali! javob 4.", h) == "Javob 4."
    assert ai._strip_repeat_greeting("Здравствуйте! Ответ 4.", h) == "Ответ 4."
    assert ai._strip_repeat_greeting("2*2 = 4.", h) == "2*2 = 4."
    assert ai._strip_repeat_greeting("Salom!", h) == "Salom!"  # bo'sh qolmasin
    assert ai._strip_repeat_greeting("Salomatligingiz yaxshimi?", h) == "Salomatligingiz yaxshimi?"


def test_greeting_kept_when_user_greets_again():
    h = [U("salom"), A("Assalomu alaykum! Men Anna."), U("[ovozli xabar] Assalomu alaykum, yaxshimisiz")]
    assert ai._strip_repeat_greeting("Va alaykum assalom! Rahmat.", h) == "Va alaykum assalom! Rahmat."


def test_image_refusal_is_not_a_description():
    assert ai._REFUSAL.search("Kechirasiz, lekin bu turdagi tasvirlarni ta'riflay olmayman.")
    assert ai._REFUSAL.search("I'm sorry, I can't help with that.")
    assert not ai._REFUSAL.search("Rasmda qorli maydonda oq avtomobil yonida yigit turibdi.")
