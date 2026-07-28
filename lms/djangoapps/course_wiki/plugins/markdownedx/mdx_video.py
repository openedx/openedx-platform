#!/usr/bin/env python
# TODO: Is this file still used? If so it should be refactored and tests added.
# pylint: disable=line-too-long, invalid-name
"""
Embeds web videos using URLs.  For instance, if a URL to an youtube video is
found in the text submitted to markdown and it isn't enclosed in parenthesis
like a normal link in markdown, then the URL will be swapped with a embedded
youtube video.

All resulting HTML is XHTML Strict compatible.

>>> import markdown

Test Metacafe

>>> s = "http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.metacafe.com/fplayer/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room.swf" height="423" type="application/x-shockwave-flash" width="498"><param name="movie" value="http://www.metacafe.com/fplayer/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room.swf" /><param name="allowFullScreen" value="true" /></object></p>'


Test Metacafe with arguments

>>> markdown.markdown(s, ['video(metacafe_width=500,metacafe_height=425)'])
u'<p><object data="http://www.metacafe.com/fplayer/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room.swf" height="425" type="application/x-shockwave-flash" width="500"><param name="movie" value="http://www.metacafe.com/fplayer/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room.swf" /><param name="allowFullScreen" value="true" /></object></p>'


Test Link To Metacafe

>>> s = "[Metacafe link](http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/)"
>>> markdown.markdown(s, ['video'])
u'<p><a href="http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/">Metacafe link</a></p>'


Test Markdown Escaping

>>> s = "\\http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/"
>>> markdown.markdown(s, ['video'])
u'<p>http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/</p>'
>>> s = "`http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/`"
>>> markdown.markdown(s, ['video'])
u'<p><code>http://www.metacafe.com/watch/yt-tZMsrrQCnx8/pycon_2008_django_sprint_room/</code></p>'


Test Youtube

>>> s = "http://www.youtube.com/watch?v=u1mA-0w8XPo&hd=1&fs=1&feature=PlayList&p=34C6046F7FEACFD3&playnext=1&playnext_from=PL&index=1"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.youtube.com/v/u1mA-0w8XPo&amp;hd=1&amp;fs=1&amp;feature=PlayList&amp;p=34C6046F7FEACFD3&amp;playnext=1&amp;playnext_from=PL&amp;index=1" height="344" type="application/x-shockwave-flash" width="425"><param name="movie" value="http://www.youtube.com/v/u1mA-0w8XPo&amp;hd=1&amp;fs=1&amp;feature=PlayList&amp;p=34C6046F7FEACFD3&amp;playnext=1&amp;playnext_from=PL&amp;index=1" /><param name="allowFullScreen" value="true" /></object></p>'


Test Youtube with argument

>>> markdown.markdown(s, ['video(youtube_width=200,youtube_height=100)'])
u'<p><object data="http://www.youtube.com/v/u1mA-0w8XPo&amp;hd=1&amp;fs=1&amp;feature=PlayList&amp;p=34C6046F7FEACFD3&amp;playnext=1&amp;playnext_from=PL&amp;index=1" height="100" type="application/x-shockwave-flash" width="200"><param name="movie" value="http://www.youtube.com/v/u1mA-0w8XPo&amp;hd=1&amp;fs=1&amp;feature=PlayList&amp;p=34C6046F7FEACFD3&amp;playnext=1&amp;playnext_from=PL&amp;index=1" /><param name="allowFullScreen" value="true" /></object></p>'


Test Youtube Link

>>> s = "[Youtube link](http://www.youtube.com/watch?v=u1mA-0w8XPo&feature=PlayList&p=34C6046F7FEACFD3&playnext=1&playnext_from=PL&index=1)"
>>> markdown.markdown(s, ['video'])
u'<p><a href="http://www.youtube.com/watch?v=u1mA-0w8XPo&amp;feature=PlayList&amp;p=34C6046F7FEACFD3&amp;playnext=1&amp;playnext_from=PL&amp;index=1">Youtube link</a></p>'


Test Dailymotion

>>> s = "http://www.dailymotion.com/relevance/search/ut2004/video/x3kv65_ut2004-ownage_videogames"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.dailymotion.com/swf/x3kv65_ut2004-ownage_videogames" height="405" type="application/x-shockwave-flash" width="480"><param name="movie" value="http://www.dailymotion.com/swf/x3kv65_ut2004-ownage_videogames" /><param name="allowFullScreen" value="true" /></object></p>'


Test Dailymotion again (Dailymotion and their crazy URLs)

>>> s = "http://www.dailymotion.com/us/video/x8qak3_iron-man-vs-bruce-lee_fun"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.dailymotion.com/swf/x8qak3_iron-man-vs-bruce-lee_fun" height="405" type="application/x-shockwave-flash" width="480"><param name="movie" value="http://www.dailymotion.com/swf/x8qak3_iron-man-vs-bruce-lee_fun" /><param name="allowFullScreen" value="true" /></object></p>'


Test Yahoo! Video

>>> s = "http://video.yahoo.com/watch/1981791/4769603"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://d.yimg.com/static.video.yahoo.com/yep/YV_YEP.swf?ver=2.2.40" height="322" type="application/x-shockwave-flash" width="512"><param name="movie" value="http://d.yimg.com/static.video.yahoo.com/yep/YV_YEP.swf?ver=2.2.40" /><param name="allowFullScreen" value="true" /><param name="flashVars" value="id=4769603&amp;vid=1981791" /></object></p>'


Test Veoh Video

>>> s = "http://www.veoh.com/search/videos/q/mario#watch%3De129555XxCZanYD"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.veoh.com/videodetails2.swf?permalinkId=e129555XxCZanYD" height="341" type="application/x-shockwave-flash" width="410"><param name="movie" value="http://www.veoh.com/videodetails2.swf?permalinkId=e129555XxCZanYD" /><param name="allowFullScreen" value="true" /></object></p>'


Test Veoh Video Again (More fun URLs)

>>> s = "http://www.veoh.com/group/BigCatRescuers#watch%3Dv16771056hFtSBYEr"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.veoh.com/videodetails2.swf?permalinkId=v16771056hFtSBYEr" height="341" type="application/x-shockwave-flash" width="410"><param name="movie" value="http://www.veoh.com/videodetails2.swf?permalinkId=v16771056hFtSBYEr" /><param name="allowFullScreen" value="true" /></object></p>'


Test Veoh Video Yet Again (Even more fun URLs)

>>> s = "http://www.veoh.com/browse/videos/category/anime/watch/v181645607JyXPWcQ"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.veoh.com/videodetails2.swf?permalinkId=v181645607JyXPWcQ" height="341" type="application/x-shockwave-flash" width="410"><param name="movie" value="http://www.veoh.com/videodetails2.swf?permalinkId=v181645607JyXPWcQ" /><param name="allowFullScreen" value="true" /></object></p>'


Test Vimeo Video

>>> s = "http://www.vimeo.com/1496152"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://vimeo.com/moogaloop.swf?clip_id=1496152&amp;amp;server=vimeo.com" height="321" type="application/x-shockwave-flash" width="400"><param name="movie" value="http://vimeo.com/moogaloop.swf?clip_id=1496152&amp;amp;server=vimeo.com" /><param name="allowFullScreen" value="true" /></object></p>'

Test Vimeo Video with some GET values

>>> s = "http://vimeo.com/1496152?test=test"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://vimeo.com/moogaloop.swf?clip_id=1496152&amp;amp;server=vimeo.com" height="321" type="application/x-shockwave-flash" width="400"><param name="movie" value="http://vimeo.com/moogaloop.swf?clip_id=1496152&amp;amp;server=vimeo.com" /><param name="allowFullScreen" value="true" /></object></p>'

Test Blip.tv

>>> s = "http://blip.tv/file/get/Pycon-PlenarySprintIntro563.flv"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://blip.tv/scripts/flash/showplayer.swf?file=http://blip.tv/file/get/Pycon-PlenarySprintIntro563.flv" height="300" type="application/x-shockwave-flash" width="480"><param name="movie" value="http://blip.tv/scripts/flash/showplayer.swf?file=http://blip.tv/file/get/Pycon-PlenarySprintIntro563.flv" /><param name="allowFullScreen" value="true" /></object></p>'

Test Gametrailers

>>> s = "http://www.gametrailers.com/video/console-comparison-borderlands/58079"
>>> markdown.markdown(s, ['video'])
u'<p><object data="http://www.gametrailers.com/remote_wrap.php?mid=58079" height="392" type="application/x-shockwave-flash" width="480"><param name="movie" value="http://www.gametrailers.com/remote_wrap.php?mid=58079" /><param name="allowFullScreen" value="true" /></object></p>'
"""
from xml.etree import ElementTree

import markdown

version = "0.1.6"


class VideoExtension(markdown.Extension):  # pylint: disable=missing-class-docstring
    def __init__(self, **kwargs):
        self.config = {
            'bliptv_width': ['480', 'Width for Blip.tv videos'],
            'bliptv_height': ['300', 'Height for Blip.tv videos'],
            'dailymotion_width': ['480', 'Width for Dailymotion videos'],
            'dailymotion_height': ['405', 'Height for Dailymotion videos'],
            'gametrailers_width': ['480', 'Width for Gametrailers videos'],
            'gametrailers_height': ['392', 'Height for Gametrailers videos'],
            'metacafe_width': ['498', 'Width for Metacafe videos'],
            'metacafe_height': ['423', 'Height for Metacafe videos'],
            'veoh_width': ['410', 'Width for Veoh videos'],
            'veoh_height': ['341', 'Height for Veoh videos'],
            'vimeo_width': ['400', 'Width for Vimeo videos'],
            'vimeo_height': ['321', 'Height for Vimeo videos'],
            'yahoo_width': ['512', 'Width for Yahoo! videos'],
            'yahoo_height': ['322', 'Height for Yahoo! videos'],
            'youtube_width': ['425', 'Width for Youtube videos'],
            'youtube_height': ['344', 'Height for Youtube videos'],
        }

        # Override defaults with user settings
        super().__init__(**kwargs)

    def add_inline(self, md, name, klass, re):  # pylint: disable=invalid-name
        """Adds the inline link"""
        pattern = klass(re)
        pattern.md = md
        pattern.ext = self
        md.inlinePatterns.register(pattern, name, 170)

    def extendMarkdown(self, md):  # pylint: disable=arguments-differ, unused-argument
        self.add_inline(md, 'bliptv', Bliptv,
                        r'([^(]|^)http://(\w+\.|)blip.tv/file/get/(?P<bliptvfile>\S+.flv)')
        self.add_inline(md, 'dailymotion', Dailymotion,
                        r'([^(]|^)http://www\.dailymotion\.com/(?P<dailymotionid>\S+)')
        self.add_inline(md, 'gametrailers', Gametrailers,
                        r'([^(]|^)http://www.gametrailers.com/video/[a-z0-9-]+/(?P<gametrailersid>\d+)')
        self.add_inline(md, 'metacafe', Metacafe,
                        r'([^(]|^)http://www\.metacafe\.com/watch/(?P<metacafeid>\S+)/')
        self.add_inline(md, 'veoh', Veoh,
                        r'([^(]|^)http://www\.veoh\.com/\S*(#watch%3D|watch/)(?P<veohid>\w+)')
        self.add_inline(md, 'vimeo', Vimeo,
                        r'([^(]|^)http://(www.|)vimeo\.com/(?P<vimeoid>\d+)\S*')
        self.add_inline(md, 'yahoo', Yahoo,
                        r'([^(]|^)http://video\.yahoo\.com/watch/(?P<yahoovid>\d+)/(?P<yahooid>\d+)')
        self.add_inline(md, 'youtube', Youtube,
                        r'([^(]|^)http://www\.youtube\.com/watch\?\S*v=(?P<youtubeargs>[A-Za-z0-9_&=-]+)\S*')


class Bliptv(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://blip.tv/scripts/flash/showplayer.swf?file=http://blip.tv/file/get/%s' % m.group('bliptvfile')  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['bliptv_width'][0]
        height = self.ext.config['bliptv_height'][0]
        return flash_object(url, width, height)


class Dailymotion(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://www.dailymotion.com/swf/%s' % m.group('dailymotionid').split('/')[-1]  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['dailymotion_width'][0]
        height = self.ext.config['dailymotion_height'][0]
        return flash_object(url, width, height)


class Gametrailers(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://www.gametrailers.com/remote_wrap.php?mid=%s' % \
            m.group('gametrailersid').split('/')[-1]  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['gametrailers_width'][0]
        height = self.ext.config['gametrailers_height'][0]
        return flash_object(url, width, height)


class Metacafe(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://www.metacafe.com/fplayer/%s.swf' % m.group('metacafeid')  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['metacafe_width'][0]
        height = self.ext.config['metacafe_height'][0]
        return flash_object(url, width, height)


class Veoh(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://www.veoh.com/videodetails2.swf?permalinkId=%s' % m.group('veohid')  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['veoh_width'][0]
        height = self.ext.config['veoh_height'][0]
        return flash_object(url, width, height)


class Vimeo(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://vimeo.com/moogaloop.swf?clip_id=%s&amp;server=vimeo.com' % m.group('vimeoid')  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['vimeo_width'][0]
        height = self.ext.config['vimeo_height'][0]
        return flash_object(url, width, height)


class Yahoo(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = "http://d.yimg.com/static.video.yahoo.com/yep/YV_YEP.swf?ver=2.2.40"
        # pylint: disable=no-member
        width = self.ext.config['yahoo_width'][0]
        height = self.ext.config['yahoo_height'][0]
        obj = flash_object(url, width, height)
        param = ElementTree.Element('param')
        param.set('name', 'flashVars')
        param.set('value', "id={}&vid={}".format(m.group('yahooid'), m.group('yahoovid')))
        obj.append(param)
        return obj


class Youtube(markdown.inlinepatterns.Pattern):  # pylint: disable=missing-class-docstring
    def handleMatch(self, m):
        url = 'http://www.youtube.com/v/%s' % m.group('youtubeargs')  # noqa: UP031
        # pylint: disable=no-member
        width = self.ext.config['youtube_width'][0]
        height = self.ext.config['youtube_height'][0]
        return flash_object(url, width, height)


def flash_object(url, width, height):  # pylint: disable=missing-function-docstring
    obj = ElementTree.Element('object')
    obj.set('type', 'application/x-shockwave-flash')
    obj.set('width', width)
    obj.set('height', height)
    obj.set('data', url)
    param = ElementTree.Element('param')
    param.set('name', 'movie')
    param.set('value', url)
    obj.append(param)
    param = ElementTree.Element('param')
    param.set('name', 'allowFullScreen')
    param.set('value', 'true')
    obj.append(param)
    return obj


def makeExtension(**kwargs):
    return VideoExtension(**kwargs)


if __name__ == "__main__":
    import doctest
    doctest.testmod()
