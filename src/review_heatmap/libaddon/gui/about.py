# -*- coding: utf-8 -*-

# Libaddon for Anki
#
# Copyright (C) 2018-2019  Aristotelis P. <https//glutanimate.com/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version, with the additions
# listed at the end of the license file that accompanied this program.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# NOTE: This program is subject to certain additional terms pursuant to
# Section 7 of the GNU Affero General Public License.  You should have
# received a copy of these additional terms immediately following the
# terms and conditions of the GNU Affero General Public License that
# accompanied this program.
#
# If not, please request a copy through one of the means of contact
# listed here: <https://glutanimate.com/contact/>.
#
# Any modifications to this file must keep this entire header intact.

"""
Generate 'about' info, including credits, copyright, etc.
"""

from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

from ..consts import ADDON
from ..platform import ANKI20

if not ANKI20:
    string = str
else:
    import string

libs_header = (
    "<p>{} ships with the following third-party code:</p>".format(ADDON.NAME))

html_template = """\
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">
<html>
<head>
    <style type="text/css">
        ul {{
            margin-top: 0px; margin-bottom: 0px; margin-left: 0px; margin-right: 0px; -qt-list-indent: 1;
        }}
        li {{
            margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;
        }}
        body {{
            margin-left: 10px; margin-right: 10px;
        }}
    </style>
</head>
<body>
    {title}
    <p><h3>Credits</h3></p>
    <p>This is a modified fork of the original add-on.</p>
    <p>Based on the Anki add-on
        <a href="https://github.com/glutanimate/review-heatmap/">Review Heatmap</a>
        by Glutanimate.</p>
    {authors_string}
    {libs_string}
    
    {members_string}
    
    <p><h3>License</h3></p>
    <p><i>{display_name}</i> is <b>free and open-source</b> software. The add-on code that runs within
        Anki is released under the {license} license, extended by a number of additional terms.
        For more information please see the license file that accompanied this program.</p>
    <p>This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY.
        Please see the license file for more details.</p>
    
    {debugging}
</body>
</html>\
"""

debugging_template = """\
<p><h3>Debugging</h3></p>
<p>Please don't use any of the following features unless instructed to:</p>
<ul>
    <li><a href="action://debug-toggle">Toggle debugging</a></li>
    <li><a href="action://debug-copy">Copy log to clipboard</a></li>
    <li><a href="action://debug-open">Open log</a></li>
    <li><a href="action://debug-clear">Clear log</a></li>
</ul>\
"""

authors_template = """\
<p>© {years} <a href="{contact}">{name}</a></p>\
"""

libs_item_template = """\
<li><a href="{url}">{name}</a> ({version}), © {author}, {license}</li>\
"""

title_template = """\
<h3>About {display_name} (v{version})</h3>\
"""

def getAboutString(title=False, showDebug=False):
    authors_string = "\n".join(authors_template.format(**dct)
                               for dct in ADDON.AUTHORS)
    libs_entries = "\n".join(libs_item_template.format(**dct)
                             for dct in ADDON.LIBRARIES)
    if libs_entries:
        libs_string = "\n".join((libs_header, "<ul>", libs_entries, "</ul>"))
    else:
        libs_string = ""
    contributors_string = "<p>With patches from: <i>{}</i></p>".format(
        ", ".join(sorted(ADDON.CONTRIBUTORS, key=string.lower))
    )

    members = list(ADDON.MEMBERS_TOP) + list(ADDON.MEMBERS_CREDITED)
    members_string = (
        "<p>Original project supporters: {}</p>".format(", ".join(members))
        if members else ""
    )

    if title:
        title_string = title_template.format(display_name=ADDON.NAME,
                                             version=ADDON.VERSION)
    else:
        title_string = ""
        
    if showDebug:
        debugging = debugging_template
    else:
        debugging = ""

    return html_template.format(display_name=ADDON.NAME,
                                license=ADDON.LICENSE,
                                title=title_string,
                                authors_string=authors_string,
                                libs_string=libs_string,
                                contributors_string=contributors_string,
                                members_string=members_string,
                                debugging=debugging)
