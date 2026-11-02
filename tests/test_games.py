import io
import json
import time

import pytest
from PIL import Image

from buddy import actions, ai, desktop, games
from buddy.engine import Engine


class FakeAI:
    """Stands in for the model. Replies come off a list, and each prompt is kept to check what it was told."""

    def __init__(self):
        self.replies = []
        self.prompts = []

    def chat(self, model, system, prompt, image=None, timeout=240, temperature=0.1):
        self.prompts.append((system, prompt, image))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def talk(self, model, system, messages, timeout=240, max_tokens=2000, temperature=0.3):
        self.prompts.append((system, messages, None))
        return self.replies.pop(0)


@pytest.fixture
def model(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(ai, "chat", fake.chat)
    monkeypatch.setattr(ai, "talk", fake.talk)
    monkeypatch.setattr(ai, "can_see", lambda name: True)
    monkeypatch.setattr(desktop, "target_window", lambda: None)
    monkeypatch.setattr("buddy.config.load", lambda: {"agent_enabled": True, "ai_model": "m", "agent_max_steps": 5})
    return fake


def play(engine, text, **kwargs):
    accepted = engine.submit(text, **kwargs)
    deadline = time.monotonic() + 3
    while engine.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    return accepted


def said(engine):
    return [event["text"] for event in engine.events if event["kind"] in ("say", "report")][-1]


def moods(engine):
    return [event["text"] for event in engine.events if event["kind"] == "mood"]


def test_asking_for_each_game():
    cases = {"play 20 questions": "twenty", "let's play twenty questions": "twenty", "Play guess my secret": "secret",
             "can we play riddles": "riddles", "give me a riddle": "riddles", "ask me a riddle": "riddles", "riddles": "riddles",
             "play story time": "story", "let's write a story together": None, "play draw and guess": "draw", "play pictionary": "draw",
             "play a game": "menu", "let's play a game": "menu", "what games can you play": "menu", "I'm bored": "menu",
             "are there any text games": "menu", "text games": "menu", "play adventure": "adventure", "let's play a text adventure": "adventure",
             "play hangman": "hangman", "hang man": "hangman", "play word chain": "chain", "word chain": "chain"}
    for phrase, key in cases.items():
        if key:
            assert games.wanted(phrase) == key, phrase


def test_other_things_are_not_games():
    for phrase in ["play believer", "tell me a story", "play the song riddles of love", "what time is it", "open chrome", "next song"]:
        assert games.wanted(phrase) is None, phrase


def test_answers():
    cases = {"yes": "yes", "Yes.": "yes", "yeah it is": "yes", "అవును": "yes", "avunu": "yes", "no": "no", "Nope!": "no", "it isn't": "no",
             "కాదు": "no", "not really": "no", "maybe": "maybe", "not sure": "maybe", "I don't know": "maybe", "a banana": None, "nothing": None}
    for phrase, answer in cases.items():
        assert games.answer_of(phrase) == answer, phrase


def test_a_game_takes_everything_said_so_no_command_runs(model, monkeypatch):
    ran = []
    monkeypatch.setitem(actions.REGISTRY, "power", lambda mode: ran.append(mode) or "done")
    monkeypatch.setitem(actions.REGISTRY, "open_app", lambda **kwargs: ran.append("open") or "done")
    engine = Engine()
    model.replies = ["It began. Then it wobbled.", "It fell. It rose.", "Oh no. Oh yes."]
    play(engine, "play story time")
    play(engine, "shut down the laptop")
    play(engine, "open chrome")
    assert ran == [] and engine.pending is None and engine.game is not None


def test_yes_in_a_game_is_never_a_confirmation(model, monkeypatch):
    ran = []
    monkeypatch.setitem(actions.REGISTRY, "power", lambda mode: ran.append(mode) or "done")
    engine = Engine()
    play(engine, "shut down the laptop")
    assert "Are you sure" in said(engine)
    model.replies = [{"question": "Is it big?", "is_guess": False}]
    play(engine, "play 20 questions")  # anything but yes cancels what was waiting
    play(engine, "yes")
    assert ran == [] and engine.game is not None and engine.pending is None


def test_buddy_by_name_steps_outside_the_game(model, monkeypatch):
    ran = []
    monkeypatch.setitem(actions.REGISTRY, "next_track", lambda: ran.append("next") or "Next song")
    engine = Engine()
    model.replies = ["It began. Then it wobbled.", "More. And more."]
    play(engine, "play story time")
    play(engine, "next song")  # part of the story
    assert ran == []
    play(engine, "Buddy, next song")
    assert ran == ["next"] and engine.game is not None


def test_stop_and_quit_end_a_game(model):
    engine = Engine()
    play(engine, "play guess my secret")
    play(engine, "stop")
    assert engine.game is None
    play(engine, "play guess my secret")
    play(engine, "quit")
    assert engine.game is None and "game over" in said(engine).lower()


def test_an_idle_game_goes_away_and_commands_work_again(model, monkeypatch):
    ran = []
    monkeypatch.setitem(actions.REGISTRY, "next_track", lambda: ran.append("next") or "Next song")
    engine = Engine()
    play(engine, "play guess my secret")
    engine.game.last_turn -= 60 * 60
    play(engine, "next song")
    assert ran == ["next"] and engine.game is None


def test_a_game_from_whatsapp_leaves_the_laptop_mic_alone(model, monkeypatch):
    ran = []
    monkeypatch.setitem(actions.REGISTRY, "next_track", lambda: ran.append("next") or "Next song")
    engine = Engine()
    play(engine, "play guess my secret", source="whatsapp")
    assert "quit" in said(engine).lower()
    assert not engine.listening()
    play(engine, "next song")  # said at the laptop, while the game is on the phone
    assert ran == ["next"] and engine.game is not None


def test_a_game_can_be_played_on_whatsapp_without_being_read_aloud(model):
    spoken = []
    engine = Engine(speaker=type("Speaker", (), {"say": lambda self, text: spoken.append(text), "speaking": False, "stop": lambda self: None})())
    model.replies = [{"question": "Is it big?", "is_guess": False}]
    play(engine, "play 20 questions", source="whatsapp")
    for _ in games.OPENING_QUESTIONS[1:]:
        play(engine, "no", source="whatsapp")
    play(engine, "no", source="whatsapp")
    assert said(engine) == "Is it big?" and spoken == []


def test_a_laptop_game_listens_without_the_wake_word(model):
    engine = Engine()
    play(engine, "play guess my secret")
    model.replies = [{"answer": "yes", "named_it": False}]
    assert engine.listening()
    assert play(engine, "is it alive", require_wake=True)


def guess_turn(engine, model, thing):
    """Answers no up to the first turn on which the code has the model guess."""
    while len(engine.game.asked) + 2 < games.GUESS_FROM + 1:
        model.replies = [{"question": f"Question number {len(engine.game.asked)}?", "candidates": ["a cat"]}]
        model.replies[0]["candidates"] = [] if len(engine.game.asked) + 2 < games.GUESS_FROM else [thing]
        play(engine, "no")
    if not engine.game.is_guess:
        model.replies = [{"question": "Another?", "candidates": [thing]}]
        play(engine, "no")


def test_twenty_questions_guesses_by_itself_as_it_goes(model):
    engine = Engine()
    play(engine, "play 20 questions")
    guess_turn(engine, model, "a phone")
    assert engine.game.is_guess and len(engine.game.asked) < games.MAX_QUESTIONS


def test_twenty_questions_buddy_wins_on_a_right_guess(model):
    engine = Engine()
    play(engine, "play 20 questions")
    guess_turn(engine, model, "a phone")
    assert said(engine) == "My guess: is it a phone?"
    play(engine, "yes")
    assert engine.game is None and moods(engine) == ["cheer"]


def test_twenty_questions_a_wrong_guess_carries_on(model):
    engine = Engine()
    play(engine, "play 20 questions")
    guess_turn(engine, model, "a phone")
    model.replies = [{"question": "Is it a tablet?", "candidates": ["a phone", "a tablet"]}]
    play(engine, "no")
    assert engine.game is not None and moods(engine) == ["shock"] and said(engine) == "Is it a tablet?"
    assert "a phone" in model.prompts[-1][1]


def test_twenty_questions_only_a_real_guess_wins(model):
    engine = Engine()
    play(engine, "play 20 questions")
    model.replies = [{"question": f"Question number {n}?", "is_guess": False} for n in range(16)]
    for _ in range(19):
        play(engine, "no")
    play(engine, "yes")  # the last question, which wasn't a guess
    assert engine.game is None and moods(engine)[-1] == "sulk"


def test_twenty_questions_asks_for_a_real_answer(model):
    engine = Engine()
    play(engine, "play 20 questions")
    play(engine, "a banana")
    assert "yes, no or not sure" in said(engine).lower() and engine.game.asked == []


def test_twenty_questions_ends_after_twenty(model):
    engine = Engine()
    play(engine, "play 20 questions")
    model.replies = [{"question": f"Question number {n}?", "is_guess": False} for n in range(16)]
    for _ in range(20):
        play(engine, "no")
    assert engine.game is None and moods(engine)[-1] == "sulk"


def test_twenty_questions_does_not_repeat_itself(model):
    engine = Engine()
    play(engine, "play 20 questions")
    for _ in games.OPENING_QUESTIONS[1:]:
        play(engine, "no")
    model.replies = [{"question": games.OPENING_QUESTIONS[0], "is_guess": False}, {"question": "Something new?", "is_guess": False}]
    play(engine, "no")
    assert said(engine) == "Something new?"


def secret_game(engine, monkeypatch, secret="dog"):
    monkeypatch.setitem(games.SECRETS, "an animal", (secret,))
    monkeypatch.setattr(games.random, "choice", lambda options: options[0] if not isinstance(options, tuple) or options != tuple(games.SECRETS) else "an animal")
    play(engine, "play guess my secret")
    assert engine.game.secret == secret


def test_the_secret_answer_only_ever_comes_from_the_fixed_list(model, monkeypatch):
    engine = Engine()
    secret_game(engine, monkeypatch)
    model.replies = [{"answer": "It's a dog, obviously", "named_it": False}]
    play(engine, "tell me what the secret is please")
    assert "dog" not in said(engine).lower() and engine.game is not None


def test_a_hint_that_gives_it_away_is_replaced(model, monkeypatch):
    engine = Engine()
    secret_game(engine, monkeypatch)
    model.replies = [{"hint": "It's a dog"}, {"hint": "Dogs bark"}]
    play(engine, "hint")
    assert "dog" not in said(engine).lower() and "letters" in said(engine)


def test_naming_the_secret_wins_but_mentioning_it_does_not(model, monkeypatch):
    engine = Engine()
    secret_game(engine, monkeypatch)
    model.replies = [{"answer": "no", "named_it": False}]
    play(engine, "is it bigger than a dog")
    assert engine.game is not None
    play(engine, "is it a dog?")
    assert engine.game is None and moods(engine) == ["shock"]


def test_giving_up_reveals_it(model, monkeypatch):
    engine = Engine()
    secret_game(engine, monkeypatch)
    play(engine, "I give up")
    assert "dog" in said(engine) and engine.game is None


def riddle(answer="clock"):
    return {"riddle": f"What is {answer[::-1]}?", "answer": answer, "hint": "It ticks"}


def test_riddles_are_scored_and_finish(model):
    engine = Engine()
    model.replies = [riddle("clock")]
    play(engine, "play riddles")
    model.replies = [riddle("river")]
    play(engine, "a clock")
    assert engine.game.right == 1 and "revir" in said(engine)
    model.replies = [riddle("egg"), riddle("candle"), riddle("shadow")]
    play(engine, "skip")
    play(engine, "skip")
    play(engine, "skip")
    play(engine, "skip")
    assert engine.game is None and "1 out of 5" in said(engine)


def test_a_riddle_that_contains_its_answer_is_thrown_away(model):
    engine = Engine()
    model.replies = [{"riddle": "What clock is a clock?", "answer": "clock"}, riddle("river")]
    play(engine, "play riddles")
    assert engine.game.answer == "river"


def test_a_wrong_riddle_answer_gets_one_more_try(model):
    engine = Engine()
    model.replies = [riddle("clock")]
    play(engine, "play riddles")
    model.replies = [{"correct": False}]
    play(engine, "a banana")
    assert engine.game.round == 1 and "try" in said(engine)
    model.replies = [{"correct": False}, riddle("river")]
    play(engine, "an apple")
    assert engine.game.round == 2 and "clock" in said(engine)


def test_the_story_is_kept_short_and_finishes(model):
    engine = Engine()
    model.replies = ["One. Two. Three. Four."]
    play(engine, "play story time")
    assert said(engine).startswith("One. Two. What")
    model.replies = ["Five. Six. Seven."]
    play(engine, "a goat arrives")
    assert {"role": "user", "content": "a goat arrives"} in model.prompts[-1][1]
    model.replies = ["Everyone cheered. Then more."]
    play(engine, "the end")
    assert engine.game is None and said(engine).startswith("Everyone cheered. The end")


def picture():
    output = io.BytesIO()
    Image.new("RGBA", (900, 900), (255, 0, 0, 0)).save(output, "PNG")
    return output.getvalue()


def test_a_drawing_is_prepared_for_the_model():
    with Image.open(io.BytesIO(games.sketch_jpeg(picture()))) as image:
        assert image.format == "JPEG" and max(image.size) == games.DRAWING_SIDE
        assert image.getpixel((5, 5)) == (255, 255, 255)  # what was see-through is white


def test_draw_and_guess(model):
    engine = Engine()
    play(engine, "play draw and guess")
    assert engine.game_status()["drawing"] is True
    model.replies = [{"guess": "a cat"}]
    engine.submit_drawing(b"jpeg")
    deadline = time.monotonic() + 3
    while engine.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert said(engine) == "Is it a cat?" and model.prompts[-1][2] == b"jpeg"
    model.replies = [{"guess": "a tiger"}]
    play(engine, "no")
    assert "a cat" in model.prompts[-1][1] and "tiger" in said(engine)
    play(engine, "yes")
    assert engine.game.buddy == 1 and engine.game.round == 2 and moods(engine)[-1] == "dance"


def test_buddy_gets_three_guesses_at_a_drawing(model):
    engine = Engine()
    play(engine, "play draw and guess")
    model.replies = [{"guess": "a cat"}, {"guess": "a dog"}, {"guess": "a cow"}]
    engine.submit_drawing(b"jpeg")
    time.sleep(0.05)
    play(engine, "no")
    play(engine, "no")
    play(engine, "no")
    assert engine.game.player == 1 and engine.game.round == 2


def test_on_whatsapp_a_photo_is_the_drawing(model):
    class Photo:
        name, note, text, pictures = "photo.jpg", "", "", [b"photo"]

    engine = Engine()
    play(engine, "play draw and guess", source="whatsapp")
    assert "photo" in said(engine)
    model.replies = [{"guess": "a house"}]
    engine.submit_file("", Photo())
    deadline = time.monotonic() + 3
    while engine.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert said(engine) == "Is it a house?" and model.prompts[-1][2] == b"photo"


def test_draw_and_guess_needs_a_model_that_can_see(model, monkeypatch):
    monkeypatch.setattr(ai, "can_see", lambda name: False)
    engine = Engine()
    play(engine, "play draw and guess")
    assert engine.game is None and "Settings" in said(engine)


def test_a_drawing_with_no_game_is_turned_away(model):
    engine = Engine()
    engine.submit_drawing(b"jpeg")
    assert "Draw and Guess" in said(engine) and not engine.busy


def test_a_garbled_reply_costs_the_turn_and_not_the_game(model):
    engine = Engine()
    play(engine, "play 20 questions")
    for _ in games.OPENING_QUESTIONS[1:]:
        play(engine, "no")
    asked = engine.game.question
    model.replies = ["not json", "still not json"]
    play(engine, "no")
    assert said(engine) == games.SLIPPED and engine.game is not None and engine.game.question == asked


def test_failing_on_the_first_turn_leaves_no_game(model):
    engine = Engine()
    model.replies = [RuntimeError("boom")]
    play(engine, "play riddles")
    assert engine.game is None and "banana" in said(engine)


def test_a_missing_model_ends_the_game_and_says_why(model):
    engine = Engine()
    model.replies = [ai.NotReady("The local AI isn't installed yet.")]
    play(engine, "play riddles")
    assert engine.game is None and "isn't installed" in said(engine)


def test_games_need_the_local_ai_switched_on(model, monkeypatch):
    monkeypatch.setattr("buddy.config.load", lambda: {"agent_enabled": False, "ai_model": "m"})
    engine = Engine()
    play(engine, "play riddles")
    assert engine.game is None and "turned off" in said(engine)


def test_one_more_replays_the_last_game(model):
    engine = Engine()
    play(engine, "play guess my secret")
    play(engine, "give up")
    assert engine.game is None
    play(engine, "one more")
    assert engine.game.key == "secret"


def test_silly_requests_move_the_character(model):
    engine = Engine()
    play(engine, "do a dance")
    assert moods(engine) == ["dance"] and said(engine) == "Watch this!"
    play(engine, "make a funny face")
    assert moods(engine)[-1] == "face"
    play(engine, "do a dance", source="whatsapp")
    assert "laptop" in said(engine)


def test_ending_a_game_when_none_is_on_doesnt_reach_the_agent(model):
    engine = Engine()
    play(engine, "stop the game")
    assert "aren't playing" in said(engine)


def test_game_turns_stay_out_of_later_conversation(model):
    engine = Engine()
    play(engine, "play guess my secret")
    model.replies = [{"answer": "no", "named_it": False}]
    play(engine, "is it alive")
    assert engine.recent == []


def test_telugu_turns_write_in_telugu(model):
    engine = Engine()
    play(engine, "play 20 questions")
    model.replies = ["ఇది తినవచ్చా?"]
    play(engine, "కాదు")
    assert "Telugu" in model.prompts[-1][0] and said(engine) == "ఇది తినవచ్చా?"


def test_hindi_and_german_turns_write_in_that_language(model):
    for answer, reply, expected in (("नहीं", "क्या यह खाया जा सकता है?", "Hindi"), ("nein", "Kann man es essen?", "German")):
        engine = Engine()
        play(engine, "play 20 questions")
        model.replies = [reply]
        play(engine, answer)
        assert engine.game.asked[-1][1] == "no" and expected in model.prompts[-1][0] and said(engine) == reply


def test_hindi_and_german_quick_answers():
    cases = {"हाँ": "yes", "हां।": "yes", "haan": "yes", "ja": "yes", "Ja.": "yes", "genau": "yes", "नहीं": "no", "nahi": "no", "nein": "no",
             "शायद": "maybe", "पता नहीं": "maybe", "vielleicht": "maybe", "weiß nicht": "maybe"}
    for phrase, answer in cases.items():
        assert games.answer_of(phrase) == answer, phrase


def test_the_menu_in_chat_lists_every_game(model):
    engine = Engine()
    play(engine, "are there any text games?", source="whatsapp")
    for game in games.GAMES.values():
        assert game.title in said(engine), game.title


def scene(text="You trip."):
    return {"scene": text, "choices": ["Run", "Hide", "Dance"]}


def test_adventure_offers_numbered_choices_and_takes_a_number(model):
    engine = Engine()
    model.replies = [scene()]
    play(engine, "play adventure", source="whatsapp")
    assert "1. Run\n2. Hide\n3. Dance" in said(engine)
    model.replies = [scene("You dance badly.")]
    play(engine, "3", source="whatsapp")
    assert "The hero does this: Dance" in model.prompts[-1][1] and said(engine).startswith("You dance badly.")


def test_adventure_takes_the_players_own_idea_and_ends(model):
    engine = Engine()
    model.replies = [scene()]
    play(engine, "play adventure")
    model.replies = [scene()] * 7 + [{"scene": "Everyone claps.", "choices": []}]
    play(engine, "I eat the map")
    assert "The hero does this: I eat the map" in model.prompts[-1][1]
    for _ in range(7):
        play(engine, "1")
    assert engine.game is None and "The end" in said(engine) and "last turn" in model.prompts[-1][0]


def hangman(engine, word="mango"):
    play(engine, "play hangman")
    engine.game.secret = word
    return engine.game


def test_hangman_shows_found_letters_and_counts_lives(model):
    engine = Engine()
    game = hangman(engine)
    play(engine, "the letter a")
    assert said(engine).endswith("_ a _ _ _")
    play(engine, "z")
    assert game.lives == games.HANGMAN_LIVES - 1
    play(engine, "z")
    assert game.lives == games.HANGMAN_LIVES - 1 and "already" in said(engine)


def test_hangman_won_by_letters_or_by_the_word(model):
    engine = Engine()
    hangman(engine, "egg")
    play(engine, "E.")
    play(engine, "g")
    assert engine.game is None and "egg" in said(engine)
    hangman(engine, "mango")
    play(engine, "is it a mango?")
    assert engine.game is None and moods(engine)[-1] == "shock"


def test_hangman_lost_and_reveals_the_word(model):
    engine = Engine()
    hangman(engine, "mango")
    for letter in "bcdefh":
        play(engine, letter)
    assert engine.game is None and "mango" in said(engine) and moods(engine)[-1] == "cheer"


def test_hangman_needs_no_ai(model, monkeypatch):
    monkeypatch.setattr("buddy.config.load", lambda: {"agent_enabled": False, "ai_model": "m"})
    engine = Engine()
    hangman(engine)
    play(engine, "a")
    assert engine.game is not None and model.prompts == []


def chain(engine, word="mango"):
    play(engine, "play word chain", source="whatsapp")
    engine.game.word, engine.game.used = word, [word]
    return engine.game


def test_word_chain_checks_the_rules_for_the_player(model):
    engine = Engine()
    game = chain(engine)
    play(engine, "banana", source="whatsapp")
    assert game.lives == games.CHAIN_LIVES - 1 and "doesn't start with o" in said(engine)
    play(engine, "two words", source="whatsapp")
    assert game.lives == games.CHAIN_LIVES - 1
    model.replies = [{"real": False}]
    play(engine, "oxqz", source="whatsapp")
    assert game.lives == games.CHAIN_LIVES - 2
    model.replies = [{"real": True}, {"word": "elephant"}]
    play(engine, "orange", source="whatsapp")
    assert game.word == "elephant" and "starts with t" in said(engine) and "orange" in game.used
    play(engine, "elephant", source="whatsapp")
    assert engine.game is None and "I win" in said(engine)


def test_word_chain_checks_the_rules_for_the_model_too(model):
    engine = Engine()
    game = chain(engine)
    model.replies = [{"real": True}, {"word": "mango"}, {"word": "zebra"}, {"word": "egg"}]
    play(engine, "orange", source="whatsapp")
    assert game.word == "egg"


def test_word_chain_buddy_loses_when_it_has_no_word(model):
    engine = Engine()
    chain(engine)
    model.replies = [{"real": True}, {"word": "zebra"}, {"word": "zebra"}, {"word": "zebra"}]
    play(engine, "orange", source="whatsapp")
    assert engine.game is None and "beat me" in said(engine)
