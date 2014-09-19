#!/usr/bin/env python
# -*- coding:utf-8 -*-
import re
import time
import traceback
from string import Template
from tornado import gen
from tornado.web import asynchronous

from base import BaseHandler
from libs import utils
from libs.paginator import Paginator
import tornadoasyncmemcache as memcache

import qiniu.rs
import qiniu.io
import tasks
import tcelery
tcelery.setup_nonblocking_producer()

CATEGORIES_TIMEOUT = 3600
PRODUCTS_TIMEOUT = 3600
SUBJECTS_TIMEOUT = 3600
SUBJECT_ITEMS_TIMEOUT = 3600
PRODUCT_TIMEOUT = 3600

EMAIL_REGEX = re.compile(r'^[_a-z0-9-]+(\.[_a-z0-9-]+)*@[a-z0-9-]+(\.[a-z0-9-]+)*(\.[a-z]{2,4})$')
USERNAME_REGEX = re.compile(ur"^[\u4e00-\u9fa5_a-zA-Z0-9]{4,30}$")
DEFAULT_PHOTO = 'http://shopa.qiniudn.com/defaultavatar.png'

class ApiHandler(BaseHandler):
	def __init__(self, *args, **kwds):
		super(BaseHandler, self).__init__(*args, **kwds)	

	def prepare(self):
		self.set_header('Content-type', 'application/json; charset=utf-8')

	def render(self, template, **kwargs):
		kwargs['stamp2time'] = utils.stamp2time
  		super(BaseHandler, self).render(template, **kwargs)

  	def write_error(self, status_code, **kwargs):
  		exc = traceback.format_exc() + "\n"
  		with open('./error.log', 'a') as file:
  			file.write(exc)

  		super(BaseHandler, self).write_error(status_code, **kwargs)

  	def check_xsrf_cookie(self):
  		pass

  	def getCategories(self, args, callback):
  		self.db.execute('SELECT * FROM '+utils.getTableName('category', args['site'])+' WHERE id!=0 ORDER BY sort DESC')
  		callback(self.db.get_rows(is_dict=True))

	@property
	def mc(self):
		return memcache.ClientPool(['127.0.0.1:11211'], maxclients=100)

class CategoriesHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
		except Exception:
			self.send_error(500)
			return

		cacheName = 'categories_'+str(args['site'])
		categories = yield gen.Task(self.mc.get, cacheName)
		if categories is None:
			categories = yield gen.Task(self.getCategories, args)
			yield gen.Task(self.mc.set, cacheName, categories, CATEGORIES_TIMEOUT)
		self.render('api/categories.html', categories=categories)


class ProductsHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['page'] = int(self.get_argument('page'))
			args['site'] = int(self.get_argument('site'))
			args['category'] = self.get_argument('category')
		except Exception:
			self.send_error(500)
			return

		if args['page'] < 1: args['page'] = 1

		cacheName = 'products_'+str(args['site'])+'_'+str(args['category'])+'_'+str(args['page'])
		data = yield gen.Task(self.mc.get, cacheName)
		if data is None:
			data = yield gen.Task(self.getData, args)

		if data['products']:
			yield gen.Task(self.mc.set, cacheName, data, PRODUCTS_TIMEOUT)
		self.render('api/products.html', **data)

	def getData(self, args, callback):
		data = dict()
		data['page'] = args['page']
		data['per_page'] = 15
		
		self.db.execute("SELECT * FROM "+utils.getTableName('category', args['site'])+" WHERE id=%s", (args['category'],))
		if self.db.get_rows_num() == 0:
			callback(None)
			return

		self.db.execute("SELECT count(*) as total FROM "+utils.getTableName('product', args['site'])+" WHERE category=%s OR \
			pid=%s", (args['category'], args['category']))

		data['total'] = self.db.get_rows(size=1, is_dict=True)['total']
		
		paginator = Paginator(data['per_page'], data['total'], args['page'], self.request.uri)
		data['pages'] = paginator.totalPages

		if int(data['page']) > paginator.totalPages:
			data['products'] = []
		else:
			self.db.execute("SELECT * FROM "+utils.getTableName('product', args['site'])+" WHERE category=%s OR pid=%s ORDER BY \
				sort DESC, id DESC LIMIT %s,%s",(args['category'], args['category'], paginator.startRow, paginator.stopRows))				
			data['products'] = self.db.get_rows(is_dict=True)

		callback(data)


class RecordHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['id'] = int(self.get_argument('id'))
			args['action'] = self.get_argument('action')
			args['uid'] = self.get_argument('uid', None)
			args['sessionid'] = self.get_argument('sessionid', None)
		except Exception:
			self.send_error(500)
			return

		if args['action'] == 'addview':
			data = yield gen.Task(self.view, args)
		else:
			data = yield gen.Task(self.like, args)

		self.render('api/record.html', **data)

	def like(self, args, callback):
		data = {'status_code': 0, 'message': u'success', 'current_count': 0}

		if args['uid'] is None or args['sessionid'] is None:
			data['status_code'] = 1
			data['message'] = u'缺少用户信息'
			callback(data)
			return

		self.db.execute("SELECT * FROM "+utils.getTableName('user', args['site'])+" WHERE id=%s AND sessionid=%s \
			LIMIT 1", (args['uid'], args['sessionid']))
		if self.db.get_rows_num() == 0:
			data['status_code'] = 2
			data['message'] = u'用户登录信息不正确'
			callback(data)
			return

		self.db.execute("SELECT * FROM "+utils.getTableName('product', args['site'])+" WHERE id=%s", (args['id'],))
		if self.db.get_rows_num() == 0:
			data['status_code'] = 3
			data['message'] = u'商品id不存在'
			callback(data)
			return

		product = self.db.get_rows(size=1, is_dict=True)
		if args['action'] == 'addlike':
			self.db.execute("UPDATE "+utils.getTableName('product', args['site'])+" SET likes=likes+1 WHERE id=%s", (args['id'],))
			self.db.execute("SELECT * FROM "+utils.getTableName('like', args['site'])+" WHERE uid=%s AND product=%s", (args['uid'], args['id']))
			if self.db.get_rows_num() == 0:
				self.db.insert(utils.getTableName('like', args['site']), {'product': args['id'], 'uid': args['uid'], 'time': int(time.time())})
			data['current_count'] = product['likes'] + 1
		else:
			self.db.execute("UPDATE "+utils.getTableName('product', args['site'])+" SET likes=likes-1 WHERE id=%s AND likes>0", (args['id'],))
			self.db.delete(utils.getTableName('like', args['site']), {'product': args['id'], 'uid': args['uid']})
			data['current_count'] = product['likes'] - 1 if product['likes'] > 0 else 0
		callback(data)

	def view(self, args, callback):
		data = {'status_code': 0, 'message': u'success', 'current_count': 0}

		self.db.execute("SELECT * FROM "+utils.getTableName('product', args['site'])+" WHERE id=%s", (args['id'],))
		if self.db.get_rows_num() == 0:
			data['status_code'] = 1
			data['message'] = u'商品id不存在'
			callback(data)
			return

		product = self.db.get_rows(size=1, is_dict=True)
		self.db.execute("UPDATE "+utils.getTableName('product', args['site'])+" SET view=view+1 WHERE id=%s", (args['id'],))
		data['current_count'] = product['view'] + 1
		callback(data)


class LikesHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['uid'] = self.get_argument('uid')
			args['sessionid'] = self.get_argument('sessionid')
		except Exception:
			self.send_error(500)
			return

		data = yield gen.Task(self.getLike, args)
		self.render('api/likes.html', **data)

	def getLike(self, args, callback):
		data = {'status_code': 0, 'message': u'success', 'likes': {}}
		self.db.execute("SELECT * FROM "+utils.getTableName('user', args['site'])+" WHERE id=%s AND sessionid=%s", (args['uid'], args['sessionid']))
		if self.db.get_rows_num() == 0:
			data['status_code'] = 1
			data['message'] = u'用户登录信息不正确'
			callback(data)
			return

		self.db.execute("SELECT p.*, l.uid FROM "+ utils.getTableName('product', args['site'])+" p,"+ utils.getTableName('like', args['site'])+" l \
			WHERE p.id=l.product AND uid=%s",(args['uid'],))
		data['products'] = self.db.get_rows(is_dict=True)
		callback(data)


class LoginHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['nickname'] = self.get_argument('nickname')
			args['password'] = self.get_argument('password')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.validate, args)
		self.write(result)
		self.finish()

	def validate(self, args, callback):
		result = dict()
		self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" WHERE nickname=%s", (args['nickname'],))
		if self.db.get_rows_num() == 0:
			result['status'] = '1'
			callback(result)
			return

		self.db.execute("SELECT id, nickname, email, gender, photo, sessionid, platformid FROM "+utils.getTableName('user', args['site'])+" WHERE \
			nickname=%s AND password=%s AND platformid=0 LIMIT 1", (args['nickname'], utils.encrypt(args['password'])))
		if self.db.get_rows_num() == 0:
			result['status'] = '2'
			callback(result)
		else:
			result['status'] = '0'
			user = self.db.get_rows(size=1, is_dict=True)
			user['id'] = str(user['id'])
			user['gender'] = str(user['gender'])
			callback(dict(result.items() + user.items()))


class RegisterHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['gender'] = self.get_argument('gender')
			args['nickname'] = self.get_argument('nickname')
			args['email'] = self.get_argument('email')
			args['password'] = self.get_argument('password')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doRegister, args)
		self.write(result)
		self.finish()

	def doRegister(self, args, callback):
		result = dict()
		if not USERNAME_REGEX.match(args['nickname']):
			result['status'] = '1'
			callback(result)
			return

		if not EMAIL_REGEX.match(args['email']):
			result['status'] = '2'
			callback(result)
			return

		if len(args['password']) < 6 or len(args['password']) > 16:
			result['status'] = '3'
			callback(result)
			return

		self.db.execute("SELECT * FROM "+utils.getTableName('user', args['site'])+" WHERE nickname=%s", (args['nickname'],))
		if self.db.get_rows_num() != 0:
			result['status'] = '4'
			callback(result)
			return

		self.db.execute("SELECT * FROM "+utils.getTableName('user', args['site'])+" WHERE email=%s", (args['email'],))
		if self.db.get_rows_num() != 0:
			result['status'] = '5'
			callback(result)
			return

		try:
			data = dict(
				nickname = args['nickname'],
				password = utils.encrypt(args['password']),
				email = args['email'],
				gender = args['gender'],
				sessionid = utils.createSessionId(),
				time = int(time.time())
			)

			self.db.execute("SELECT * FROM avatar WHERE gender=%s ORDER BY rand() LIMIT 1", (data['gender']))
			if self.db.get_rows_num() != 0:
				avatar = self.db.get_rows(size=1, is_dict=True)
				data['photo'] = avatar['avatar']
				self.db.update('avatar', {'used': 1}, {'id': avatar['id']})
			else:
				data['photo'] = DEFAULT_PHOTO

			self.db.insert(utils.getTableName('user', args['site']), data)
			result['status'] = '0'
			data['id'] = self.db.cursor.lastrowid
			data['platformid'] = '0'
			del data['password']
			del data['time']
			callback(dict(result.items() + data.items()))
		except Exception:
			result['status'] = '6'
			callback(result)


class OauthHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['oauthid'] = self.get_argument('oauthid')
			args['nickname'] = self.get_argument('nickname')
			args['photo'] = self.get_argument('photo', DEFAULT_PHOTO)
			args['gender'] = self.get_argument('gender', '0')
			args['platformid'] = self.get_argument('platformid')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doOauthLogin, args)
		self.write(result)
		self.finish()

	def doOauthLogin(self, args, callback):
		self.db.execute("SELECT id, nickname, email, gender, photo, sessionid, platformid FROM "+utils.getTableName('user', args['site'])+" \
			WHERE oauthid=%s AND platformid=%s LIMIT 1", (args['oauthid'], args['platformid']))

		result = dict()
		if self.db.get_rows_num() == 0:
			#register
			self.db.execute("SELECT * FROM "+utils.getTableName('user', args['site'])+" WHERE nickname=%s", (args['nickname'],))
			if self.db.get_rows_num() != 0:
				result['status'] = '1'
			else:
				result['status'] = '0'
				password = utils.encrypt(utils.randomword())
				data = {'nickname': args['nickname'], 'password': password, \
					'gender': args['gender'], 'photo': args['photo'], 'oauthid': args['oauthid'], \
					'sessionid': utils.createSessionId(), 'platformid': args['platformid'], 'time': int(time.time())}
				self.db.insert(utils.getTableName('user', args['site']), data)

				result['id'] = self.db.cursor.lastrowid
				result['nickname'] = args['nickname']
				result['email'] = ''
				result['gender'] = args['gender']
				result['photo'] = args['photo']
				result['sessionid'] = data['sessionid']
				result['platformid'] = args['platformid']
				result['type'] = str(type(args['nickname']))
			callback(result)
		else:
			#login
			result['status'] = '0'
			user = self.db.get_rows(size=1, is_dict=True)
			user['id'] = str(user['id'])
			user['gender'] = str(user['gender'])
			user['email'] = '' if user['email'] is None else user['email']
			callback(dict(result.items() + user.items()))



class PushHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['registration'] = self.get_argument('registration')
			args['nickname'] = self.get_argument('nickname', '')
			args['status'] = self.get_argument('status', 1)
			args['sound'] = self.get_argument('sound', 1)
			args['vibrate'] = self.get_argument('vibrate', 1)
			args['categories'] = self.get_argument('categories', '')
			args['keyword'] = self.get_argument('keyword', None)
			args['keyword'] = args['keyword'].replace('+', '') if args['keyword'] else ''
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doPush, args)
		self.write(result)
		self.finish()

	def doPush(self, args, callback):
		result = {'status': 0}
		try:
			site = args['site']
			del args['site']
			if not args['nickname']: del args['nickname']
			self.db.execute("SELECT * FROM "+ utils.getTableName('pusher', site)+" WHERE registration=%s", (args['registration'],))
			if self.db.get_rows_num() == 0:
				self.db.insert(utils.getTableName('pusher', site), args)
			else:
				self.db.update(utils.getTableName('pusher', site), args, {'registration': args['registration']})
		except Exception:
			result['status'] = '1'
		callback(result)


class ProductHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['id'] = self.get_argument('id')
			args['site'] = self.get_argument('site')
		except Exception:
			self.send_error(500)
			return

		cacheName = 'product_' + str(args['site']) + '_' + str(args['id'])
		product = yield gen.Task(self.mc.get, cacheName)
		if product is None:
			print 11111
			product = yield gen.Task(self.getProduct, args)
			yield gen.Task(self.mc.set, cacheName, product, PRODUCT_TIMEOUT)
		self.render('api/product.html', product=product)

	def getProduct(self, args, callback):
		self.db.execute("SELECT * FROM "+ utils.getTableName('product', args['site'])+" WHERE id=%s", (args['id'],))
		callback(self.db.get_rows(size=1, is_dict=True))


class SendHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['uid'] = self.get_argument('id', '')
			args['registration'] = self.get_argument('registration')
			args['msg'] = self.get_argument('msg')
			args['time'] = int(time.time())
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSend, args)
		self.write(result)

	def doSend(self, args, callback):
		result = {'status': 0}
		try:
			site = args['site']
			del args['site']
			self.db.insert(utils.getTableName('tucao', site), args)
		except Exception:
			result['status'] = 1
		callback(result)


class MsgHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['registration'] = self.get_argument('registration')
		except Exception:
			self.send_error(500)
			return

		data = yield gen.Task(self.getMsg, args)
		self.render('api/msg.html', data=data)

	def getMsg(self, args, callback):
		self.db.execute("SELECT * FROM "+ utils.getTableName('tucao', args['site'])+" WHERE registration=%s ORDER BY time \
			LIMIT 30", (args['registration'],))
		callback(self.db.get_rows(is_dict=True))


class SendMailHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['nickname'] = self.get_argument('nickname')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.preSendMail, args)
		if result['status'] == 0:
			msg = u'''
			亲爱的$email:
您此次找回密码的验证码是：$code，请在30分钟内在找回密码页填入此验证码。
如果您并未发过此请求，则可能是因为其他用户在尝试重设密码时误输入了您的电子邮件地址而使您收到这封邮件，
那么您可以放心的忽略此邮件，无需进一步采取任何操作。
'''
			emailResponse = yield gen.Task(tasks.sendMail.apply_async, (result['email'], u'重新设置密码'.encode('utf-8'), 
				Template(msg).substitute(result).encode('utf-8') ))
			if emailResponse.result != True:
				result['status'] = 2
			else:
				result['site'] = args['site']
				yield gen.Task(self.afterSendMail, result)
		self.write(result)
		self.finish()

	def preSendMail(self, args, callback):
		result = {'status': 0}
		self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" WHERE nickname=%s AND platformid=0 \
			LIMIT 1", (args['nickname'],))
		if self.db.get_rows_num() == 0:
			result['status'] = 1
		else:
			user = self.db.get_rows(size=1, is_dict=True)
			result['uid'] = user['id']
			result['email'] = user['email']
			result['code'] = utils.randomnum()
		callback(result)

	def afterSendMail(self, args, callback):
		self.db.delete(utils.getTableName('code', args['site']), {'uid': args['uid']})
		self.db.insert(utils.getTableName('code', args['site']), {'uid': args['uid'], 'code': args['code'], \
			'token': utils.createSessionId(),'timeout': int(time.time()) + 1800 })		
		callback(True)


class VerifyHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['nickname'] = self.get_argument('nickname')
			args['verify'] = self.get_argument('verify')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doVerify, args)
		self.write(result)
		self.finish()

	def doVerify(self, args, callback):
		result = {'status': 0}
		self.db.execute("SELECT c.token FROM "+ utils.getTableName('user', args['site'])+" u, "+ utils.getTableName('code', args['site'])+\
			" c WHERE u.id=c.uid AND u.nickname=%s AND c.code=%s AND c.status=0 AND timeout>=%s LIMIT 1", (args['nickname'], args['verify'], int(time.time())))
		if self.db.get_rows_num() == 0:
			result['status'] = 1
		else:
			row = self.db.get_rows(size=1, is_dict=True)
			result['token'] = row['token']
		callback(result)


class ForgetPasswordHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['nickname'] = self.get_argument('nickname')
			args['password'] = self.get_argument('password')
			args['token'] = self.get_argument('token')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSetPassword, args)
		self.write(result)
		self.finish()

	def doSetPassword(self, args, callback):
		result = {'status': '0'}
		if len(args['password']) < 6 or len(args['password']) > 16:
			result['status'] = '1'
		else:
			self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" u, "+ utils.getTableName('code', args['site'])+\
				" c WHERE u.id=c.uid AND u.nickname=%s AND c.token=%s",\
			 	(args['nickname'], args['token']))
			if self.db.get_rows_num() == 0:
				result['status'] = '2'
			else:
				self.db.update(utils.getTableName('user', args['site']), {'password': utils.encrypt(args['password'])}, {'nickname': args['nickname']})
				self.db.execute("SELECT id, nickname, email, gender, photo, sessionid, platformid FROM "+ utils.getTableName('user', args['site'])+" \
					WHERE nickname=%s", (args['nickname'],))
				user = self.db.get_rows(size=1, is_dict=True)
				user['id'] = str(user['id'])
				user['gender'] = str(user['gender'])
				user['email'] = '' if user['email'] is None else user['email']
				self.db.update(utils.getTableName('code', args['site']), {'status': 1}, {'uid': user['id']})
		callback(dict(result.items() + user.items()) if user is not None else result)


class SetPhotoHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['uid'] = self.get_argument('uid')
			args['site'] = self.get_argument('site')
			args['image'] = self.get_argument('image')
			args['sessionid'] = self.get_argument('sessionid')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSetPhoto, args)
		self.write(result)
		self.finish()

	def doSetPhoto(self, args, callback):
		result = {'status': 0}

		try:
			self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" WHERE id=%s AND sessionid=%s \
				LIMIT 1", (args['uid'], args['sessionid']))
			if self.db.get_rows_num() == 0:
				result['status'] = 1
			else:
				image = utils.decode_base64(args['image'])
				name = utils.getPhotoName('user', 'png')
				ret, err = qiniu.io.put(self.uptoken, name, image)
				if err is not None:
					result['status'] = 2
				else:
					user = self.db.get_rows(size=1, is_dict=True)
					ret, err = qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], user['photo'].split('/').pop())
					photo = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], name)
					result['photo'] = photo
					self.db.update(utils.getTableName('user', args['site']), {'photo': photo}, {'id': args['uid']})
		except Exception:
			result['status'] = 3
			result['error'] = traceback.format_exc()
 		
 		callback(result)


class CheckUpdateHandler(ApiHandler):
 	@asynchronous
 	@gen.coroutine
 	def get(self):
 		try:
			args = dict()
			args['site'] = self.get_argument('site')
			args['package'] = self.get_argument('package', None)
			args['versonname'] = self.get_argument('versonname', None)
			args['category'] = self.get_argument('category', None)
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doCheck, args)
		self.write(result)
		self.finish()

	def doCheck(self, args, callback):
		result = dict()
		self.db.execute("SELECT * FROM %s WHERE name='category_last_updated' LIMIT 1" %utils.getTableName('config', args['site']))
		config = self.db.get_rows(size=1, is_dict=True)
		result['category_last_updated'] = int(config['value'])

		self.db.execute("SELECT * FROM %s WHERE name='subject_last_updated' LIMIT 1" %utils.getTableName('config', args['site']))
		config = self.db.get_rows(size=1, is_dict=True)
		result['subject_last_updated'] = int(config['value'])

		if args['package'] and args['versonname']:
			self.db.execute("SELECT package as packagename, versonname, versoncode, path, description FROM app WHERE package=%s \
				AND versonname>%s ORDER BY versonname DESC LIMIT 1", (args['package'], args['versonname']))
			if self.db.get_rows_num() != 0:
				result['app'] = self.db.get_rows(size=1, is_dict=True)

		if args['category']:
			self.db.execute("SELECT * FROM " + utils.getTableName('category', args['site']) +" WHERE id=%s OR pid=%s \
				ORDER BY latest DESC LIMIT 1", (args['category'], args['category']))
			category = self.db.get_rows(size=1, is_dict=True)
			if category['latest']:
				result['product_last_updated'] = int(category['latest'])
			else:
				result['product_last_updated'] = 0

		callback(result)


class SettingHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['uid'] = self.get_argument('uid')
			args['site'] = self.get_argument('site')
			args['sessionid'] = self.get_argument('sessionid')
			args['nickname'] = self.get_argument('nickname')
			args['email'] = self.get_argument('email')
			args['gender'] = self.get_argument('gender')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSetting, args)
		self.write(result)
		self.finish()

	def doSetting(self, args, callback):
		result = {'status': 0}
		self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" WHERE id=%s AND sessionid=%s LIMIT 1", \
			(args['uid'], args['sessionid']))
		if self.db.get_rows() == 0:
			result['status'] = 1
			callback(result)
			return

		if not USERNAME_REGEX.match(args['nickname']):
			result['status'] = 2
			callback(result)
			return

		if not EMAIL_REGEX.match(args['email']):
			result['status'] = 3
			callback(result)
			return

		self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" WHERE nickname=%s AND id!=%s LIMIT 1", \
			(args['nickname'], args['uid']))
		if self.db.get_rows_num() != 0:
			result['status'] = 4
			callback(result)
			return

		self.db.execute("SELECT * FROM "+ utils.getTableName('user', args['site'])+" WHERE email=%s AND id!=%s LIMIT 1", \
			(args['email'], args['uid']))
		if self.db.get_rows_num() != 0:
			result['status'] = 5
			callback(result)
			return

		self.db.update(utils.getTableName('user', args['site']), {'nickname': args['nickname'], 'email': args['email'], \
			'gender': args['gender']}, {'id': args['uid']})
		callback(result)


class SetPasswordHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['uid'] = self.get_argument('uid')
			args['site'] = self.get_argument('site')
			args['sessionid'] = self.get_argument('sessionid')
			args['password'] = self.get_argument('password')
			args['newpassword'] = self.get_argument('newpassword')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSetPassword, args)
		self.write(result)
		self.finish()

	def doSetPassword(self, args, callback):
		result = {'status': 0}
		if len(args['newpassword']) < 6 or len(args['newpassword']) > 16:
			result['status'] = 1
			callback(result)
			return

		self.db.execute("SELECT * FROM " + utils.getTableName('user', args['site'])+ " WHERE id=%s AND sessionid=%s \
			LIMIT 1", (args['uid'], args['sessionid']))
		if self.db.get_rows_num() == 0:
			result['status'] = 2
			callback(result)
			return

		self.db.execute("SELECT * FROM " + utils.getTableName('user', args['site'])+ " WHERE id=%s AND password=%s \
			LIMIT 1", (args['uid'], utils.encrypt(args['password'])))
		if self.db.get_rows_num() == 0:
			result['status'] = 3
			callback(result)
			return

		self.db.update(utils.getTableName('user', args['site']), {'password': utils.encrypt(args['newpassword'])}, {'id': args['uid']})
		callback(result)


class SubjectHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['site'] = self.get_argument('site')
		except Exception:
			self.send_error(500)
			return

		cacheName = 'subjects_' + str(args['site'])
		# subjects = yield gen.Task(self.mc.get, cacheName)
		# if subjects is None:
		subjects = yield gen.Task(self.getSubject, args)
		yield gen.Task(self.mc.set, cacheName, subjects, SUBJECTS_TIMEOUT)
		self.render('api/subject.html', subjects=subjects)

	def getSubject(self, args, callback):
		self.db.execute("SELECT * FROM %s WHERE ishow=1 ORDER BY sort desc" %utils.getTableName('subject', args['site']))
		callback(self.db.get_rows(is_dict=True))


class SubjectItemHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			args = dict()
			args['id'] = self.get_argument('id')
			args['site'] = self.get_argument('site')
		except Exception:
			self.send_error(500)
			return 

		cacheName = 'subjectItem_' + str(args['site']) + '_' + str(args['id'])
		result = yield gen.Task(self.mc.get, cacheName)
		if result is None:
			result = yield gen.Task(self.getSubjectItem, args)
			yield gen.Task(self.mc.set, cacheName, result, SUBJECT_ITEMS_TIMEOUT)
		self.render('api/subjectItem.html', **result)

	def getSubjectItem(self, args, callback):
		result = dict()
		self.db.execute("SELECT id, title, description, photo FROM " + utils.getTableName('subject', args['site'])+ " \
			WHERE id=%s ORDER BY sort DESC", (args['id'],))
		result['subject'] = self.db.get_rows(size=1, is_dict=True)

		self.db.execute("SELECT p.* FROM " + utils.getTableName('product', args['site'])+ " p, " + utils.getTableName('subitem', args['site'])+ \
			" s WHERE p.id=s.product AND s.subject=%s", (args['id'],))
		result['products'] = self.db.get_rows(is_dict=True)
		callback(result)


class IsLikeHandler(ApiHandler):
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['uid'] = self.get_argument('uid')
			args['sessionid'] = self.get_argument('sessionid')
			args['product'] = self.get_argument('product')
			args['site'] = self.get_argument('site')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doIsLike, args)
		self.write(result)
		self.finish()

	def doIsLike(self, args, callback):
		result = {'status': 0}
		self.db.execute("SELECT * FROM " + utils.getTableName('user', args['site'])+ " WHERE id=%s AND sessionid=%s LIMIT 1", \
			(args['uid'], args['sessionid']))
		if self.db.get_rows_num() == 0:
			result['status'] = 1
			callback(result)
			return

		self.db.execute("SELECT * FROM " + utils.getTableName('like', args['site'])+ " WHERE uid=%s AND product=%s LIMIT 1", \
			(args['uid'], args['product']))
		if self.db.get_rows_num() == 0:
			result['status'] = 2

		callback(result)
