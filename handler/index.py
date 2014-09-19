#! /usr/bin/env python 
# -*- coding: utf-8 -*- 

from base import BaseHandler
from tornado.web import asynchronous

class IndexHandler(BaseHandler):
	@asynchronous
	def get(self):
		self.render('home/index.html')


class TestHandler(BaseHandler):
	@asynchronous
	def get(self):
		self.write('loading')
		self.finish()