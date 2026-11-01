from buddy.parser import Context, parse


def actions(text, ctx=None):
    return [step["action"] for step in parse(text, ctx)]


def test_users_own_examples():
    assert parse("Go to YouTube.") == [{"action": "open", "name": "YouTube", "search_fallback": True}]
    assert parse("open claude") == [{"action": "open", "name": "claude", "search_fallback": False}]
    assert parse("Open VS Code.")[0]["name"] == "VS Code"
    assert actions("open vs code and send me the screenshot to my whatsapp") == [
        "open", "wait", "screenshot", "whatsapp_send_screenshot",
    ]


def test_search_after_go_to_youtube_stays_on_youtube():
    assert parse("go to youtube and search for shape of you") == [
        {"action": "youtube_search", "query": "shape of you"},
    ]


def test_search_then_play_is_one_tab():
    assert parse("go to youtube, search for shape of you and play the song") == [
        {"action": "youtube_play", "query": "shape of you"},
    ]


def test_it_refers_to_the_last_search_across_sentences():
    ctx = Context()
    parse("search for tum hi ho on youtube", ctx)
    assert parse("play it", ctx) == [{"action": "youtube_play", "query": "tum hi ho"}]


def test_context_makes_bare_search_use_youtube():
    assert actions("search for lofi", Context(on_youtube=True)) == ["youtube_search"]
    assert actions("search for lofi") == ["google_search"]


def test_and_inside_a_title_does_not_split():
    assert parse("play tom and jerry") == [{"action": "youtube_play", "query": "tom and jerry"}]


def test_dictation_keeps_case_and_ands():
    assert parse("type Hello World and open the door") == [
        {"action": "type_text", "text": "Hello World and open the door"},
    ]


def test_clause_after_typing_is_not_split_off_but_before_is():
    assert actions("open vs code and type hello") == ["open", "wait", "type_text"]


def test_browser_open_is_dropped_before_a_site():
    assert actions("open chrome and go to youtube") == ["open"]


def test_keys_and_media():
    assert parse("press control shift t") == [{"action": "press_keys", "combo": "control+shift+t"}]
    assert parse("new tab") == [{"action": "press_keys", "combo": "ctrl+t"}]
    assert parse("pause") == [{"action": "pause_media"}]
    assert parse("volume 30") == [{"action": "volume", "change": "set", "level": 30}]


def test_power_commands_parse_for_the_confirmation_step():
    assert parse("shut down the laptop") == [{"action": "power", "mode": "shutdown"}]


def test_wake_word_and_politeness_removed():
    assert actions("Hey Buddy, please open chrome.") == ["open"]
    assert parse("buddy") == []


def test_unknown_requests_fall_through_to_the_agent():
    assert parse("reply to the last email and tell them I'm late") is None
    assert parse("open chrome and do something clever") is None


def test_screenshot_variants():
    assert actions("take a screenshot") == ["screenshot"]
    assert actions("take a screenshot and send it to my whatsapp") == ["screenshot", "whatsapp_send_screenshot"]
    assert actions("send a screen shot to me on whatsapp") == ["screenshot", "whatsapp_send_screenshot"]
    assert parse("send hello to mom on whatsapp") is None


def test_every_way_of_asking_for_another_song_stays_local():
    for phrase in [
        "change the song", "Change the song.", "change song", "skip this song", "skip", "next",
        "next song", "play next song", "play the next song", "go to next song", "play another song",
        "play a different song", "change to the next song", "switch the song", "change it",
        "I don't like this song",
    ]:
        assert parse(phrase) == [{"action": "next_track"}], phrase


def test_asking_for_a_named_song():
    for phrase in ["change the song to believer", "change it to believer", "change to believer",
                   "switch the song to believer", "put on believer", "listen to believer",
                   "I want to listen to believer"]:
        assert parse(phrase) == [{"action": "youtube_play", "query": "believer"}], phrase


def test_switch_to_still_means_a_window():
    assert parse("switch to files") == [{"action": "focus_window", "name": "files"}]


def test_the_exact_sentence_that_went_to_claude():
    assert parse("open chrome and change the song") == [{"action": "next_track"}]


def test_previous_and_now_playing():
    assert parse("previous song") == [{"action": "previous_track"}]
    assert parse("go back to the last song") == [{"action": "previous_track"}]
    for phrase in ["what song is this", "what's playing", "what is playing", "what am I listening to"]:
        assert parse(phrase) == [{"action": "now_playing"}], phrase


def test_pause_and_resume():
    assert parse("stop the music") == [{"action": "pause_media"}]
    assert parse("resume") == [{"action": "resume_media"}]
    assert parse("play") == [{"action": "resume_media"}]


def test_live_screen_phrases():
    for phrase in ["share my screen", "share my screen to my phone", "stream my screen", "show my screen on my mobile",
                   "share the screen with my phone", "send my live screen to my mobile"]:
        assert parse(phrase) == [{"action": "share_screen"}], phrase
    assert parse("share my screen with control") == [{"action": "share_screen", "control": True}]
    assert parse("stop sharing my screen") == [{"action": "stop_sharing"}]


def test_whatsapp_screenshot_is_not_mistaken_for_live_sharing():
    assert actions("send my screen to my whatsapp") == ["screenshot", "whatsapp_send_screenshot"]


def test_small_talk_stays_local():
    assert parse("what time is it") == [{"action": "tell_time"}]
    assert parse("how much battery is left") == [{"action": "battery"}]
    assert parse("thank you") == [{"action": "reply", "text": "Anytime"}]


def test_system_switches():
    wifi_off = [{"action": "set_option", "option": "wifi", "on": False}]
    assert parse("turn off wi-fi") == wifi_off
    assert parse("turn wifi off") == wifi_off
    assert parse("disable the wifi") == wifi_off
    assert parse("turn on bluetooth") == [{"action": "set_option", "option": "bluetooth", "on": True}]
    assert parse("enable dark mode") == [{"action": "set_option", "option": "dark_mode", "on": True}]
    assert parse("turn on light mode") == [{"action": "set_option", "option": "dark_mode", "on": False}]
    assert parse("turn off notifications") == [{"action": "set_option", "option": "do_not_disturb", "on": True}]
    assert parse("mute") == [{"action": "volume", "change": "toggle"}]
    assert parse("turn off the mic") == [{"action": "set_option", "option": "microphone", "on": False}]


def test_brightness_and_power_profile():
    assert parse("brightness 40") == [{"action": "brightness", "change": "set", "level": 40}]
    assert parse("set the brightness to 40 percent") == [{"action": "brightness", "change": "set", "level": 40}]
    assert parse("dim the screen") == [{"action": "brightness", "change": "down"}]
    assert parse("battery saver") == [{"action": "power_profile", "profile": "power-saver"}]


def test_settings_pages_and_folders():
    assert parse("open bluetooth settings") == [{"action": "open_settings", "panel": "bluetooth"}]
    assert parse("open wi-fi settings") == [{"action": "open_settings", "panel": "wi-fi"}]
    assert parse("open my downloads") == [{"action": "open_folder", "name": "downloads"}]
    assert parse("open the music folder") == [{"action": "open_folder", "name": "music"}]
    assert parse("open settings")[0]["action"] == "open"
    assert parse("open videos")[0]["action"] == "open"


def test_names_containing_and_still_open_the_app(monkeypatch):
    installed = {"passwords and keys", "software and updates", "chrome"}  # whatever this machine happens to have
    monkeypatch.setattr("buddy.parser.find_app", lambda name: name if name.lower() in installed else None)
    assert parse("open passwords and keys")[0]["name"] == "passwords and keys"
    assert parse("open software and updates")[0]["name"] == "software and updates"
    assert parse("open chrome and do something clever") is None


def test_window_and_workspace_keys():
    assert parse("zoom in") == [{"action": "press_keys", "combo": "ctrl+plus"}]
    assert parse("next workspace") == [{"action": "press_keys", "combo": "super+pagedown"}]
    assert parse("snap left") == [{"action": "press_keys", "combo": "super+left"}]


def test_pressing_things_inside_apps_stays_local():
    assert parse("click on the sign in button") == [{"action": "click_text", "text": "sign in"}]
    assert parse("click save") == [{"action": "click_text", "text": "save"}]
    assert parse("double click report") == [{"action": "click_text", "text": "report", "count": 2}]
    assert parse("right click the desktop icon") == [{"action": "click_text", "text": "desktop", "right": True}]
    assert parse("click") == [{"action": "click"}]
    assert parse("select all") == [{"action": "press_keys", "combo": "ctrl+a"}]
    assert parse("read the screen") == [{"action": "read_screen"}]


def test_clicking_then_typing_chains():
    assert actions("click search and type hello") == ["click_text", "type_text"]


def test_lock_and_unlock_phrases():
    assert parse("lock my laptop") == [{"action": "power", "mode": "lock"}]
    for phrase in ["unlock", "unlock my laptop", "Unlock the screen."]:
        assert parse(phrase) == [{"action": "unlock_screen"}], phrase
    assert parse("is my laptop locked") == [{"action": "lock_status"}]


def test_file_commands():
    assert parse("send me report.pdf") == [{"action": "send_file", "name": "report.pdf"}]
    assert parse("send me the file report dot pdf on whatsapp") == [{"action": "send_file", "name": "report dot pdf"}]
    assert parse("send me the latest download") == [{"action": "send_file", "name": "latest download"}]
    assert parse("find report") == [{"action": "find_files", "query": "report"}]
    assert parse("find the file budget") == [{"action": "find_files", "query": "budget"}]
    assert parse("list my downloads") == [{"action": "list_folder", "name": "downloads"}]
    assert parse("what's in my documents") == [{"action": "list_folder", "name": "documents"}]
    assert parse("open the file budget") == [{"action": "open_file", "name": "budget"}]


def test_sending_still_means_screenshot_where_it_should():
    assert actions("send me a screenshot") == ["screenshot", "whatsapp_send_screenshot"]
    assert actions("take a screenshot and send it to my whatsapp") == ["screenshot", "whatsapp_send_screenshot"]
    assert parse("send hello to mom on whatsapp") is None


def test_find_on_page_and_web_searches_still_work():
    assert parse("find") == [{"action": "press_keys", "combo": "ctrl+f"}]
    assert parse("find lofi on youtube") == [{"action": "youtube_search", "query": "lofi"}]


def test_run_commands_keep_their_exact_text_and_ands():
    assert parse("run df -h") == [{"action": "run_command", "command": "df -h"}]
    assert parse("run git status and git log") == [{"action": "run_command", "command": "git status and git log"}]
    assert parse("run command ls -la") == [{"action": "run_command", "command": "ls -la"}]
    assert parse("run chrome")[0]["action"] == "open"


def test_reports():
    for phrase in ["report", "system report", "send me a report", "give me the status", "how is my laptop doing"]:
        assert parse(phrase) == [{"action": "system_report"}], phrase


def test_natural_volume_phrases_stay_local():
    for phrase in ["increase the volume", "Increase the volume of", "increase the volume of the laptop", "turn up the volume",
                   "raise the sound", "make it louder", "a bit louder", "volume up"]:
        assert parse(phrase) == [{"action": "volume", "change": "up"}], phrase
    for phrase in ["decrease the volume", "turn down the music", "make it quieter", "volume down"]:
        assert parse(phrase) == [{"action": "volume", "change": "down"}], phrase
    for phrase in ["volume 100", "set the volume to 100 percent", "max volume", "volume to full", "set sound to 100"]:
        assert parse(phrase) == [{"action": "volume", "change": "set", "level": 100}], phrase


def test_a_bare_percentage_sets_the_level_and_is_never_typed():
    for phrase in ["80%", "80 percent", "80 %", "to 80 percent", "set it to 80%"]:
        assert parse(phrase, Context()) == [{"action": "volume", "change": "set", "level": 80}], phrase
    ctx = Context()
    parse("brightness 50", ctx)
    assert parse("80 percent", ctx) == [{"action": "brightness", "change": "set", "level": 80}]


def test_softer_and_louder_in_everyday_words():
    assert parse("please make the sound a touch softer") == [{"action": "volume", "change": "down"}]
    assert parse("make the music a little louder") == [{"action": "volume", "change": "up"}]


def test_write_only_types_when_it_is_dictation():
    for request in ("write a python function to reverse a string", "write me a poem", "write some code", "write code for bubble sort"):
        assert parse(request, Context()) is None, request
    assert parse("write hello world", Context()) == [{"action": "type_text", "text": "hello world"}]
    assert parse("write down a poem", Context()) == [{"action": "type_text", "text": "a poem"}]
    assert parse("type a poem", Context()) == [{"action": "type_text", "text": "a poem"}]
