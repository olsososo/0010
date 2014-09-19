#!/usr/bin/env python
# -*- coding:utf-8 -*-
from __future__ import division
from math import ceil
from urlparse import urlparse


class Paginator():
    def __init__(self,listRows,totalRows,nowPage,uri):
        self.config = dict(
            header = u"条记录",
            prev = u"上一页",
            next = u"下一页",
            first = u"第一页",
            last = u"最后一页",
        )

        #列表每页显示行数
        self.listRows = int(listRows)
        #总行数
        self.totalRows = int(totalRows)
        #当前页数
        self.nowPage = int(nowPage)
        #uri
        self.uri = uri
        #分页栏每页显示的页数
        self.rollPage = 5
        self.uri = urlparse(self.uri).path

        #分页总页面数
        self.totalPages = int(ceil(self.totalRows/self.listRows)) if self.totalRows else 0

        self.nowPage = max(1,min(self.nowPage,self.totalPages))

        self.startRow = self.listRows*(self.nowPage-1)

        self.stopRows = self.startRow + self.listRows
        #分页的栏的页数 
        self.nowCoolPage = int(ceil(self.nowPage/self.rollPage))
        #分页的栏的总页数 
        self.coolPages = int(ceil(self.totalPages/self.rollPage))

    def show(self):
        if self.totalRows == 0:
            return

        self.upRow = self.nowPage - 1
        self.downRow = self.nowPage + 1

        upPage = "&nbsp;<a href='"+self.uri+"?p="+str(self.upRow)+"'>"+self.config['prev']+"</a>" if self.upRow > 0 else ""
        downPage = "&nbsp;<a href='"+self.uri+"?p="+str(self.downRow)+"'>"+self.config['next']+"</a>" if self.downRow <= self.totalPages else ""
        
        if self.nowCoolPage == 1:
            theFirst = "";
            prePage = ""
        else:
            preRow = self.nowPage - self.rollPage
            prePage = "&nbsp;<a href ='"+self.uri+"?p="+str(preRow)+u"'>上"+str(self.rollPage)+u"页</a>"
            theFirst = "&nbsp;<a href ='"+self.uri+"?p=1'>"+self.config['first']+"</a>"


        if self.nowCoolPage == self.coolPages:
            nextPage = ""
            theEnd = ""
        else:
            nextRow = self.nowPage + self.rollPage
            theEndRow = self.totalPages
            nextPage = "&nbsp;<a href ='"+self.uri+"?p="+str(nextRow)+u"'>下"+str(self.rollPage)+u"页</a>"
            theEnd = "&nbsp;<a href ="+self.uri+"?p="+str(theEndRow)+">"+self.config['last']+"</a>"

        linkPage = ""
        for i in range(self.rollPage):
            page = (self.nowCoolPage - 1)*self.rollPage + i + 1
            if page != self.nowPage:
                if page <= self.totalPages:
                    linkPage += "&nbsp;<a href='"+self.uri+"?p="+str(page)+"'>&nbsp;"+str(page)+"&nbsp;</a>";
                else:
                    break
            else:
                if self.totalPages != 1:
                    linkPage += "&nbsp;<span class='current'>"+str(page)+"</span>"


        pageStr = str(self.totalRows)+self.config['header']+str(self.nowPage)+"/"+str(self.totalPages)+u" 页 "+upPage+downPage+theFirst+prePage+linkPage+nextPage+theEnd

        return pageStr