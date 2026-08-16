"""Fluffy Vanilla custom emoji registry.

IDs are intentionally centralised: UI code imports semantic aliases instead of
copying raw Discord markup. Unicode stays where it communicates an action more
clearly (lock, delete, mute, etc.); branded custom emoji add identity and mood.
"""


def static(name: str, emoji_id: int) -> str:
    return f'<:{name}:{emoji_id}>'


def animated(name: str, emoji_id: int) -> str:
    return f'<a:{name}:{emoji_id}>'


# Reactions / mood
COOL = static('9428coolguyshades', 1535270936276897842)
PRIDE = static('31814gayyyy', 1535270953867804763)
CUTE = static('45529cute', 1535270968203808928)
SLUMP = static('45866slump', 1535270981176795146)
WHY = static('47634butbutwhy', 1535270995584225290)
HUH = static('67721huh', 1535271008716595200)
CUTE_ALT = static('80295cute', 1535271022880886865)
RAEP_FACE = static('84527raepface', 1535271037648769024)
BAWWW = static('93451bawww', 1535271056934314025)
PIKA = static('245609pika', 1535271074931933274)
NAUGHTY = static('300463naughty', 1535271094704013393)
FLUSTERED = static('338763flustered', 1535271113855205467)
DIZZY = static('537173dizzy', 1535271130326376459)
EXCITED = static('636510excited', 1535271148605153430)
HEART = static('772619webheartwhite', 1535271163259781192)
FLOWER = static('800962blobcatflower', 1535271178678046730)

# Decorative set with ambiguous source names. Kept available, but not assigned
# to critical actions until their visual meaning is unambiguous.
DECOR_12161 = static('12161', 1535271210147913849)
WHITE_0 = static('5519white0', 1535271227164196924)
WHITE_2 = static('995047white2', 1535271246785159300)
WHITE_3 = static('822720white3', 1535271262329376889)
WHITE_4 = static('725015white4', 1535271278775373854)
DECOR_60775 = static('60775', 1535271311687811173)
DECOR_77446 = static('77446', 1535271334341517322)
DECOR_64027 = static('64027', 1535271356579713087)
DECOR_76018 = static('76018', 1535271375189712916)
DECOR_97519 = static('97519', 1535271394261344368)

# Role / identity badges
ADMIN_PURPLE = static('1482adminlightpurple', 1535271656153677835)
BOOSTER = static('16218booster', 1535271672691691591)
OWNER = static('16739ownergradient', 1535271689347403866)
ADMIN_PINK = static('44252adminpink', 1535271702462992475)
PARTNER = static('49532partner', 1535271716039692488)
ADMIN_RED = static('49548dredgradientadmin', 1535271729386234047)
ADMIN_GREEN = static('60336admingreen', 1535271742921121892)
BOT = static('68417bot', 1535271757152391229)
ADMIN_DARK = static('71188admindarkpurple', 1535271774617600041)
MEMBER = static('82382member', 1535271797392412733)
ADMIN_TURQUOISE = static('83513adminturquoise', 1535271830489669642)
ADMIN_YELLOW = static('89807yellowadmingradient', 1535271847757746186)

# Feature badges
CROWN = static('158897crown', 1535271867332431904)
EVENT = static('592053event', 1535271887595249664)
VERIFIED = static('592053verified', 1535271911297384478)
DIAMOND = static('819847diamond', 1535271926119927938)
TICKET = static('828044ticket', 1535271943350259733)
TROPHY = static('918267trophy', 1535271960387395654)
STAR = static('985872star', 1535271977520988280)

# Animated separators / calls to action
ARROW_PINK_SOFT = animated('15072animatedarrowpink2', 1535272125089316918)
ARROW_YELLOW = animated('15770animatedarrowyellow', 1535272140528418878)
ARROW_ORANGE = animated('28079animatedarroworange', 1535272156810977331)
ARROW_BLUE = animated('32877animatedarrowbluelite', 1535272169800605719)
ARROW_PINK = animated('33214animatedarrowpink', 1535272183428026408)
ARROW_WHITE = animated('51047animatedarrowwhite', 1535286040137564271)

# Semantic aliases used by shared UI.
SUCCESS = VERIFIED
ERROR = BAWWW
WARNING = HUH
INFO = PIKA
ACTION = ARROW_PINK
STAFF = ADMIN_PURPLE

CATALOG = {
    name: value for name, value in globals().copy().items()
    if name.isupper() and isinstance(value, str) and value.startswith('<')
}
