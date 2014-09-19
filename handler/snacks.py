#! /usr/bin/env python 
# -*- coding: utf-8 -*- 

from base import BaseHandler
from tornado.web import asynchronous

class IndexHandler(BaseHandler):
	@asynchronous
	def get(self):
		self.render('snacks/index.html')