import json
import urllib2
import hexchat
import threading

__module_name__ = 'Twitch IRC Userlist Fix'
__module_description__ = 'XChat/HexChat plugin that periodically retrieves the userlist for all joined channels on the Twitch IRC servers from their website. This plugin is needed for some smaller channels in which the IRC server does not respond properly to userlist requests, causing the userlist in the clients to stay empty.'
__module_version__ = '0.3.1'
__module_author__ = 'cryzed <cryzed@googlemail.com>'


TWITCH_IRC_SERVER = 'tmi.twitch.tv'
INITIAL_UPDATE_USERLIST_TIMEOUT = 3000
UPDATE_USERLIST_TIMEOUT = 15000
RETRIEVE_USERLIST_TIMEOUT = 30000
CHATTERS_URL_TEMPLATE = 'http://tmi.twitch.tv/group/user/%s/chatters'
RAW_JOIN_COMMAND_TEMPLATE = 'RECV :{0}!~{0}@{0}.tmi.twitch.tv JOIN {1}'
RAW_JOIN_COMMAND_TEMPLATE_IDENTITY = 'RECV :{0} JOIN {1}'
RAW_PART_COMMAND_TEMPLATE = 'RECV :{0}!~{0}@{0}.tmi.twitch.tv PART {1}'
RAW_MODE_COMMAND_TEMPLATE = 'RECV :{0}!~{0}@{0}.tmi.twitch.tv MODE {1} {2} {3}'


userlists = {}
userlists_updates = {}
userlists_updates_lock = threading.Semaphore()


class start_new_thread(threading.Thread):
    def __init__(self, callback, *args, **kwargs):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.callback = lambda: callback(*args, **kwargs)
        self.start()

    def run(self):
        self.callback()


def part(nickname, channel, context=hexchat):
    command = RAW_PART_COMMAND_TEMPLATE.format(nickname, channel)
    context.command(command)


def join(channel, nickname=None, identity=None, context=hexchat):
    # One of both should definitely be passed.
    assert nickname or identity

    if identity:
        command = RAW_JOIN_COMMAND_TEMPLATE_IDENTITY.format(identity, channel)
    else:
        command = RAW_JOIN_COMMAND_TEMPLATE.format(nickname, channel)
    context.command(command)


def mode(nickname, channel, flags, target, context=hexchat):
    command = RAW_MODE_COMMAND_TEMPLATE.format(nickname, channel, flags, target)
    context.command(command)


def retrieve_userlist_update_callback(userdata):
    url, channel_key = userdata
    start_new_thread(retrieve_userlist_update_thread, url, channel_key)
    return 1


def retrieve_userlist_update_thread(url, channel_key):
    try:
        response = urllib2.urlopen(url)
    except urllib2.URLError:
        return

    userlist = json.load(response)['chatters']
    with userlists_updates_lock:
        userlists_updates[channel_key] = userlist


def initial_update_userlist_callback(channel):
    update_userlist(channel)
    return 0


def update_userlist_callback(channel):
    update_userlist(channel)
    return 1


def update_userlist(channel):
    channel_key = channel.server + channel.channel

    with userlists_updates_lock:
        if not channel_key in userlists_updates:
            return

        update = userlists_updates[channel_key]
        del userlists_updates[channel_key]

    if not channel_key in userlists:
        chatters = update['viewers'] + update['moderators'] + update['staff'] + update['admins']
        map(lambda nickname: join(channel.channel, nickname, context=channel.context), chatters)
        map(lambda nickname: mode('jtv', channel.channel, '+o', nickname, channel.context), update['moderators'])
        map(lambda nickname: mode('jtv', channel.channel, '+q', nickname, channel.context), update['staff'])
        map(lambda nickname: mode('jtv', channel.channel, '+a', nickname, channel.context), update['admins'])
        userlists[channel_key] = update
        return

    userlist = userlists[channel_key]
    joined_moderators = set(update['moderators']) - set(userlist['moderators'])
    joined_staff = set(update['staff']) - set(userlist['staff'])
    joined_admins = set(update['admins']) - set(userlist['admins'])
    joined_viewers = set(update['viewers']) - set(userlist['viewers'])
    joined = joined_moderators.union(joined_staff.union(joined_admins.union(joined_viewers)))
    map(lambda nickname: join(channel.channel, nickname, context=channel.context), joined)

    chatters = set(userlist['viewers'] + userlist['moderators'] + userlist['staff'] + userlist['admins'])
    new_chatters = set(update['viewers'] + update['moderators'] + update['staff'] + update['admins'])
    parted = chatters - new_chatters
    map(lambda nickname: part(nickname, channel.channel, channel.context), parted)

    map(lambda nickname: mode('jtv', channel.channel, '+o', nickname, channel.context), joined_moderators)
    map(lambda nickname: mode('jtv', channel.channel, '+q', nickname, channel.context), joined_staff)
    map(lambda nickname: mode('jtv', channel.channel, '+a', nickname, channel.context), joined_admins)

    userlists[channel_key] = update
    return


def privmsg_callback(word, word_eol, userdata):
    identity = word[0][1:]
    nickname = identity.split('!', 1)[0]
    users = hexchat.get_list('users')

    if not nickname in [user.nick for user in users]:
        channel = word[2]
        join(channel, identity=identity)

    return hexchat.EAT_NONE


def unload_callback(hooks):
    map(hexchat.unhook, hooks)


def end_of_names_callback(word, word_eol, userdata):
    server = hexchat.get_info('server')
    if not server == TWITCH_IRC_SERVER or len(hexchat.get_list('users')) > 1:
        return

    current_channel = hexchat.get_info('channel')
    url = CHATTERS_URL_TEMPLATE % current_channel[1:]
    channel_key = server + current_channel

    hexchat.hook_timer(RETRIEVE_USERLIST_TIMEOUT, retrieve_userlist_update_callback, (url, channel_key))
    start_new_thread(retrieve_userlist_update_thread, url, channel_key)

    channel = None
    for channel in hexchat.get_list('channels'):
        if channel.server == server and channel.channel == current_channel:
            break

    # This should never happen...
    assert channel

    # Initial userlist update, approximately 3 seconds after starting retrieve_userlist_update_thread.
    hexchat.hook_timer(INITIAL_UPDATE_USERLIST_TIMEOUT, initial_update_userlist_callback, channel)
    hexchat.hook_timer(UPDATE_USERLIST_TIMEOUT, update_userlist_callback, channel)

    return hexchat.EAT_NONE


if __name__ == '__main__':
    hooks = [
        hexchat.hook_server('366', end_of_names_callback),
        hexchat.hook_server('PRIVMSG', privmsg_callback)
    ]

    hexchat.hook_unload(unload_callback, hooks)
    print __module_name__, __module_version__, 'loaded successfully.'
