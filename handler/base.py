#!/usr/bin/env python
# -*- coding:utf-8 -*-
import time
import json
import qiniu.rs
import cPickle as pickle
import tornado.web
from libs import utils

class BaseHandler(tornado.web.RequestHandler):
	def __init__(self, *args, **kwds):
		super(BaseHandler, self).__init__(*args, **kwds)

	def prepare(self):
		if self.request.path.startswith('/god') and self.request.path != self.get_login_url():
			self.checkPower()

	def checkPower(self):
		user = self.getUserInfo()
		if user is None or user.get('power', 0) < 1:
			self.send_error(404)

	def render(self, template, **kwargs):
  		if self.current_user is not None:
   	 		kwargs['user'] = self.getUserInfo()
   	 	else:
   	 		kwargs['user'] = None

   	 	if self.get_secure_cookie('opersite') is not None:
   	 		kwargs['opersite'] = self.get_secure_cookie('opersite')
   	 	else:
   	 		kwargs['opersite'] = None
   	 	kwargs['stamp2time'] = utils.stamp2time
  		super(BaseHandler, self).render(template, **kwargs)

	def get_current_user(self):
		return self.get_secure_cookie('userId')

	def doLogin(self, user):
		self.set_secure_cookie('userId', str(user['id']), expires=None, path='/', expires_days=30)
		self.mc.set(str(user['id']), pickle.dumps(user), 3600*24*30)

	def doLogout(self):
		self.mc.delete(self.current_user)
		self.clear_cookie('userId')

	def lastUpdatedCategories(self):
		self.db.update(self.getOperTableName('config'), {'value': int(time.time())}, {'name': 'category_last_updated'})

	def lastUpdatedProducts(self, id, isCategory=False):
		now = str(int(time.time()))
		if isCategory:
			self.db.update(self.getOperTableName('category'), {'latest': now}, {'id': id})
		else:
			self.db.execute("UPDATE "+self.getOperTableName('category')+" c, "+self.getOperTableName('product')+" p SET \
				c.latest='" + now + "' WHERE c.id=p.category AND p.id=%s", (id,))

	def lastUpdatedSubjects(self):
		self.db.update(self.getOperTableName('config'), {'value': int(time.time())}, {'name': 'subject_last_updated'})

	def getCategories(self, args, callback):
		self.db.execute('SELECT * FROM %s ORDER BY sort DESC' %self.getOperTableName('category'))
		callback(self.db.get_rows(is_dict=True))

	def getParentCategories(self, args, callback):
		self.db.execute('SELECT * FROM %s WHERE pid=0 ORDER by id ASC' %self.getOperTableName('category'))
		callback(self.db.get_rows(is_dict=True))

	def getSonCategories(self, pid, callback):
		self.db.execute('SELECT * FROM %s WHERE pid!=0 AND pid= ORDER BY id ASC' %self.getOperTableName('category'))
		callback(self.db.get_rows(is_dict=True))

	def getAlliances(self, args, callback):
		self.db.execute("SELECT * FROM alliance")
		callback(self.db.get_rows(is_dict=True))

	def getConfig(self, args, callback):
		self.db.execute("SELECT * FROM %s" %self.getOperTableName('config'))
		callback(self.db.get_rows(is_dict=True))

	def getUserInfo(self, userId=None):
		if userId is None:
			userId = self.current_user

		if userId is None:
			return None

		user = self.mc.get(str(userId))

		if user is not None:
			return pickle.loads(user)
		else:
			try:
				self.db.execute("SELECT * FROM admin WHERE id=%s LIMIT 1", (userId,))
				user = self.db.get_rows(size=1, is_dict=True)
				self.mc.set(userId, pickle.dumps(user), 3600*24*30)
			except Exception:
				return None
			return user
			
	@property
	def db(self):
		return self.application.db

	@property
	def mc(self):
		return self.application.mc

	@property
	def uptoken(self):
		return qiniu.rs.PutPolicy(self.application.settings['qiniu_bucket_name']).token()

	@property
	def referer(self):
		return self.request.headers.get('Referer')

	def getOperTableName(self, table):
		user = self.getUserInfo()
		return table + '_'+ str(user['opersite'])