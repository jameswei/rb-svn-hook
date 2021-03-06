#!/usr/bin/python
# -*- coding:utf8 -*-
import os
import sys
import subprocess
import urllib2
import cookielib
import base64
import re
import shelve
import datetime
import ConfigParser
import urllib

try:
    import json
except ImportError:
    import simplejson as json

from urlparse import urljoin

from .utils import get_cmd_output, split

def get_os_conf_dir():
    platform = sys.platform
    if platform.startswith('win'):
        try:
            return os.environ['ALLUSERSPROFILE']
        except KeyError:
            print >>sys.stderr, 'Unspported operation system:%s'%platform
            sys.exit(1)
    return '/etc'

def get_os_temp_dir():
    import tempfile
    return tempfile.gettempdir()

def get_os_log_dir():
    platform = sys.platform
    if platform.startswith('win'):
        return get_os_conf_dir()
    return '/var/log'

OS_CONF_DIR = get_os_conf_dir()

conf = ConfigParser.ConfigParser()

conf_file = os.path.join(OS_CONF_DIR, 'reviewboard-svn-hooks', 'conf.ini')
if not conf.read(conf_file):
    raise StandardError('invalid configuration file:%s'%conf_file)


COOKIE_FILE = os.path.join(get_os_temp_dir(), 'reviewboard-svn-hooks-cookies.txt')

DEBUG = conf.getint('common', 'debug')

def debug(s):
    if not DEBUG:
        return
    f = open(os.path.join(get_os_log_dir(), 'reviewboard-svn-hooks', 'debug.log'), 'at')
    print >>f, str(datetime.datetime.now()), s
    f.close()

RB_SERVER = conf.get('reviewboard', 'url')
USERNAME = conf.get('reviewboard', 'username')
PASSWORD = conf.get('reviewboard', 'password')


MIN_SHIP_IT_COUNT = conf.getint('rule', 'min_ship_it_count')
MIN_EXPERT_SHIP_IT_COUNT = conf.getint('rule', 'min_expert_ship_it_count')
experts = conf.get('rule', 'experts')
EXPERTS = split(experts)
review_path = conf.get('rule', 'review_path')
REVIEW_PATH = split(review_path)
ignore_path = conf.get('rule', 'ignore_path')
IGNORE_PATH = split(ignore_path)

class SvnError(StandardError):
    pass

class Opener(object):
    def __init__(self, server, username, password, cookie_file = None):
        self._server = server
        if cookie_file is None:
            cookie_file = COOKIE_FILE
        self._auth = base64.b64encode(username + ':' + password)
        cookie_jar = cookielib.MozillaCookieJar(cookie_file)
        cookie_handler = urllib2.HTTPCookieProcessor(cookie_jar)
        self._opener = urllib2.build_opener(cookie_handler)

    def open(self, path, ext_headers, *a, **k):
        url = urljoin(self._server, path)
        return self.abs_open(url, ext_headers, *a, **k)

    def abs_open(self, url, ext_headers, *a, **k):
        debug('url open:%s' % url)
        r = urllib2.Request(url)
        for k, v in ext_headers:
            r.add_header(k, v)
        r.add_header('Authorization', 'Basic ' + self._auth)
        try:
            rsp = self._opener.open(r)
            return rsp.read()
        except urllib2.URLError, e:
            raise SvnError(str(e))

def make_svnlook_cmd(directive, repos, txn):
    def get_svnlook():
        platform = sys.platform
        if platform.startswith('win'):
            return get_cmd_output(['where svnlook']).split('\n')[0].strip()
        return 'svnlook'

    cmd =[get_svnlook(), directive, '-t',  txn, repos]
    debug(cmd)
    return cmd

def get_review_id(repos, txn):
    svnlook = make_svnlook_cmd('log', repos, txn)
    log = get_cmd_output(svnlook)
    debug(log)
    rid = re.search(r'review:([0-9]+)', log, re.M | re.I)
    if rid:
        return rid.group(1)
    raise SvnError('No review id.')

def add_to_rid_db(rid):
    USED_RID_DB = shelve.open(os.path.join(get_os_conf_dir(),
        'reviewboard-svn-hooks',
        'rb-svn-hooks-used-rid.db'))
    if USED_RID_DB.has_key(rid):
        raise SvnError, "review-id(%s) is already used."%rid
    USED_RID_DB[rid] = rid
    USED_RID_DB.sync()
    USED_RID_DB.close()

def check_rb(repos, txn):
    rid = get_review_id(repos, txn)
    path = 'api/review-requests/' + str(rid) + '/reviews/'
    opener = Opener(RB_SERVER, USERNAME, PASSWORD)
    rsp = opener.open(path, {})
    reviews = json.loads(rsp)
    debug(reviews)
    if reviews['stat'] != 'ok':
        raise SvnError, "get reviews error."
    ship_it_users = set()
    for item in reviews['reviews']:
        ship_it = int(item['ship_it'])
        if ship_it:
            ship_it_users.add(item['links']['user']['title'])
    
    if len(ship_it_users) < MIN_SHIP_IT_COUNT:
        raise SvnError, "not enough of ship_it."
    expert_count = 0
    for user in ship_it_users:
        if user in EXPERTS:
            expert_count += 1
    if expert_count < MIN_EXPERT_SHIP_IT_COUNT:
        raise SvnError, 'not enough of key user ship_it.'

    svndiff = make_svnlook_cmd("diff", repos, txn)
    diff_from_svn = get_cmd_output(svndiff)

    diff_from_svn = format_diff(diff_from_svn)
    diff_from_rb = format_diff(get_rb_diff(rid))
    debug("diff_from_svn:\n" + diff_from_rb)
    debug("diff_from_svn:\n" + diff_from_svn)
    if str(diff_from_rb) != str(diff_from_svn):
        raise SvnError, 'the diffs do not match.'
    add_to_rid_db(rid)

def is_ignorable(changed):
    for line in changed.split('\n'):
        if not line.strip():
            continue
        f = line[4:]
        flg = False
        for ignore_path in IGNORE_PATH:
            if ignore_path in f:
                flg = True
                break
        if not flg:
            return False
    return True

def get_rb_diff(rid):
    # Log in reviewboard
    login_path = "account/login/"
    cj = cookielib.CookieJar()
    opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
    urllib2.install_opener(opener)
    url = urljoin(RB_SERVER, login_path)
    resp = urllib2.urlopen(url)
    data = urllib.urlencode(dict(username=USERNAME, password=PASSWORD))
    page = opener.open(url, data).read()

    if page.find("<title>Log In") != -1:
        raise SvnError, "Login failed!"
        return

    diff_path = "r/%s/diff/raw" % rid
    diff_url = urljoin(RB_SERVER, diff_path)
    response = urllib2.open(diff_url)
    diff =  response.read()
    return diff

def format_diff(diff):
    def ignore_del_diff(li):
        '''
        the patch generated from svn client, do not contained the del file info.
        So need to del these info in the patch generated from svn server.
        @param li:
        @return:
        '''
        l = []
        for i in li:
            if not i[0].startswith("Deleted: "): # and not i[0].startswith("Added: "):
                l.append(i)
        return l

    splitIndex = []
    noBlankLineDiff = []
    fileSlice = []
    diffList = diff.splitlines()
    formatedContent = ""

    for i in diffList: # delete blank lines in patch file
        if i.strip():
            noBlankLineDiff.append(i)

    for n, c in enumerate(noBlankLineDiff):
        if c == "===================================================================":
            splitIndex.append(n)
        else:
            continue

    for x, y in enumerate(splitIndex):
        if x < len(splitIndex) - 1:
            start = int(y) - 1
            end = int(splitIndex[x+1]) - 1
            fileSlice.append(noBlankLineDiff[start: end])
        else:
            start = int(y) - 1
            fileSlice.append(noBlankLineDiff[start:])

    ignored = ignore_del_diff(fileSlice)
    for i in xrange(len(ignored)):
        formatedContent +=  "\n".join(ignored[i][4:]) + "\n"
    return formatedContent

def _main():
    debug('command:' + str(sys.argv))
    repos = sys.argv[1]
    txn = sys.argv[2]

    debug("repos:" + repos)
    debug("txn:" + txn)

    svnlook = make_svnlook_cmd('changed', repos, txn)
    changed = get_cmd_output(svnlook)
    debug(changed)


    if is_ignorable(changed):
        return

    if not REVIEW_PATH:
        check_rb(repos, txn)
        return 

    for line in changed.split('\n'):
        f = line[4:]
        for review_path in REVIEW_PATH:
            if review_path in f:
                check_rb(repos, txn)
                return

def main():
    try:
        _main()
    except SvnError, e:
        print >> sys.stderr, str(e)
        exit(1)
    except Exception, e:
        print >> sys.stderr, str(e)
        import traceback
        traceback.print_exc(file=sys.stderr)
        exit(1)
    else:
        exit(0)