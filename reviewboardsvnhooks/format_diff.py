#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = 'XuHao'

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