#! /usr/bin/env python 
# -*- coding: utf-8 -*- 
import re
import time
import traceback
from json import loads
from urllib import urlencode
from tornado import gen
from tornado import httpclient
from tornado.web import asynchronous, authenticated
import qiniu.io
import qiniu.rs

import tasks
from libs import utils
from libs.paginator import Paginator
from base import BaseHandler
from form.god import LoginForm, AdminAddForm 

import tcelery
tcelery.setup_nonblocking_producer()

class LoginHandler(BaseHandler):
	@asynchronous
	@gen.coroutine
	def get(self, template_variables={}):
		template_variables['errors'] = template_variables.get('errors')
		template_variables['sites'] = yield gen.Task(self.getSite, None)
		self.render("god/login.html", recaptcha_publickey=self.application.settings['recaptcha_publickey'],
			captcha=self.application.settings['captcha'], **template_variables
		)

	def getSite(self, args, callback):
		self.db.execute("SELECT * FROM site")
		callback(self.db.get_rows(is_dict=True))

	@asynchronous
	@gen.coroutine
	def post(self):
		form = LoginForm(self)
		if not form.validate():
			self.get({'errors':form.errors})
			return

		if self.application.settings['captcha']:
			url = 'http://www.google.com/recaptcha/api/verify?'
			challenge = self.get_argument('recaptcha_challenge_field')
			response = self.get_argument('recaptcha_response_field')
			data = {
				'privatekey': self.application.settings['recaptcha_privatekey'],
				'remoteip': self.request.remote_ip,
				'challenge': challenge,
				'response': response
			}

			client = httpclient.AsyncHTTPClient()
			recaptcharRes = yield gen.Task(client.fetch, url+urlencode(data))
			recaptcharRes = recaptcharRes.body.split()

			if(recaptcharRes[0] == 'false'):
				self.get({'errors': {'title': [u'验证码不正确']}})
				return

		name = form.name.data
		password = form.password.data
		opersite = self.get_argument('opersite')

		site = yield gen.Task(self.getOperSite, opersite)

		if not site:
			self.get({'errors': {'title': [u'无效的站点']}})
			return

		user = yield gen.Task(self.authenticated, {'name':name, 'password': password, 'opersite': opersite})
		self.set_secure_cookie('opersite', site, expires=None, path='/', expires_days=30)
		self.doLogin(user)
		self.redirect('/god/main')

	def getOperSite(self, opersite, callback):
		self.db.execute("SELECT * FROM site WHERE id=%s", (opersite))
		if self.db.get_rows_num() != 0:
			site = self.db.get_rows(size=1, is_dict=True)
			callback(site['name'])
		else:
			callback(None)

	def authenticated(self, args, callback):
		self.db.execute('SELECT * FROM admin WHERE name=%s ',(args['name'],))
		if self.db.get_rows_num() != 1:
			self.get({'errors': {'title': [u'用户名不存在']}})
			return

		query = "SELECT * FROM admin WHERE name=%s AND password=%s LIMIT 1"
		self.db.execute(query, (args['name'], utils.encrypt(args['password'])))

		if self.db.get_rows_num() != 1:
			self.get({'errors': {'title': [u'用户名和密码无法匹配']}})
			return

		user = self.db.get_rows(size=1, is_dict=True)
		self.db.update('admin', {'opersite': args['opersite']}, {'id': user['id']})
		user['opersite'] = args['opersite']

		callback(user)


class LogoutHandler(BaseHandler):
	@asynchronous
	def get(self):
		self.doLogout()
		self.redirect(self.get_login_url())


class MainHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		self.render('god/main.html')


class CategoriesHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		categories = yield gen.Task(self.getCategories, None)
		parentCategories = yield gen.Task(self.getParentCategories, None)
		self.render('god/categories.html', categories=categories, parentCategories=parentCategories)

	def getCategories(self, args, callback):
		self.db.execute('SELECT son.*,parent.name as parent FROM %s son, %s parent WHERE son.pid=parent.id \
			AND parent.pid=0 AND son.id>0' %(self.getOperTableName('category'), self.getOperTableName('category')))
		callback(self.db.get_rows(is_dict=True))


class CategoryAddHnadler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		categories = yield gen.Task(self.getParentCategories, None)
		self.render('god/categoryAdd.html', categories=categories)

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			data = dict()
			arguments = ['pid', 'name', 'description', 'sort']
			for argument in arguments:
				data[argument] = self.get_arguments(argument)

			data['photo'] = self.request.files.get('photo')
		except Exception:
			self.send_error(500)
			return

		if len(data['name']) == 0:
			self.send_error(500)
			return 

		result = yield gen.Task(self.insertCategories, data)
		if(result == True):
			self.render('god/success.html', url='/god/categories')
		else:
			self.render('god/error.html', message=result, url='/god/category/add')

	def insertCategories(self, args, callback):
		try:
			for key, name in enumerate(args['name']):
				self.db.execute("SELECT * FROM " + self.getOperTableName('category')+ " WHERE name=%s LIMIT 1", (name,))
				if self.db.get_rows_num() != 0:
					raise ValueError, '名称已经存在'

				k = utils.getPhotoName('category', args['photo'][key]['filename'].split('.').pop())
				qiniu.io.put(self.uptoken, k, data=args['photo'][key]['body'])
				photoName = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], k)
				self.db.insert(self.getOperTableName('category'), {'pid':args['pid'][key], 'name': name, 'photo': photoName,\
					'description': args['description'][key], 'sort': args['sort'][key]})

			self.lastUpdatedCategories()
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class CategoryEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine	
	def post(self):
		data = dict()
		try:
			arguments = ['id', 'pid', 'name', 'photoName','description', 'sort']
			for argument in arguments:
				data[argument] = self.get_argument(argument)
			data['islock'] = self.get_argument('islock', 0)
			data['photo'] = self.request.files.get('photo')
		except Exception:
			self.send_error(500)

		result = yield gen.Task(self.editCategory, data)

		if(result == True):
			self.render('god/success.html', url='/god/categories')
		else:
			self.render('god/error.html', message=result, url='/god/categories')

	def editCategory(self, args, callback):
		try:
			self.db.execute("SELECT * FROM " + self.getOperTableName('category')+ " WHERE name=%s AND id!=%s", \
				(args['name'], args['id']))
			if self.db.get_rows_num() !=0:
				raise ValueError, '名称已经存在'

			if args['photo'] is not None:
				k = utils.getPhotoName('category', args['photo'][0]['filename'].split('.').pop())
				ret, err = qiniu.io.put(self.uptoken, k, data=args['photo'][0]['body'])
				if err is not None:
					raise IOError, err

				ret, err = qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], args['photoName'].split('/').pop())
				newPhotoName = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], k)	

				self.db.update(self.getOperTableName('category'), {'pid': args['pid'],'name': args['name'], 'description': \
					args['description'],'sort': args['sort'], 'photo': newPhotoName,'islock': args['islock']}, {'id': args['id']})
			else:
				self.db.update(self.getOperTableName('category'), {'pid': args['pid'],'name': args['name'], 'description':\
					args['description'],'sort': args['sort'], 'islock': args['islock']}, {'id': args['id']})

			self.lastUpdatedCategories()
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class categoryDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.deleteCategory, id)
		if(result == True):
			self.render('god/success.html', url='/god/categories')
		else:
			self.render('god/error.html', message=result, url='/god/categories')

	def deleteCategory(self, id, callback):
		try:
			self.db.execute("SELECT * FROM " + self.getOperTableName('category') + " WHERE id=%s LIMIT 1", (id,))
			category = self.db.get_rows(is_dict=True)
			ret, err = qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], category[0]['photo'].split('/').pop())
			if err is not None:
				raise IOError, err

			self.db.delete(self.getOperTableName('task'), {'category': id})
			self.db.delete(self.getOperTableName('product'), {'category': id})
			self.db.delete(self.getOperTableName('category'), {'id': id})

			self.lastUpdatedCategories()
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class CategoriesDataHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			pid = self.get_argument('pid')
		except Exception:
			self.send_error(500)
			return

		self.set_header('Content-type', 'application/json; charset=utf-8')

		categories = yield gen.Task(self.getData, pid)
		self.render('god/categoriesData.html', categories=categories)

	def getData(self, pid, callback):
		self.db.execute("SELECT * FROM " + self.getOperTableName('category') + " WHERE pid=%s", (pid,))
		callback(self.db.get_rows(is_dict=True))


class ProductsHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		categories = yield gen.Task(self.getCategories, None)
		self.render('god/products.html', categories=categories)


class ProductsDataHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		self.set_header('Content-type', 'application/json; charset=utf-8')
		template_variables = yield gen.Task(self.getData, None)
		self.render('god/productsData.html', **template_variables)

	def getData(self, args, callback):
		data = dict()
		page = self.get_argument('page', 1)
		rows = self.get_argument('rows', 30)
		sidx = self.get_argument('sidx', '')
		sord = self.get_argument('sord', '')
		filters = self.get_argument('filters', '')

		if sidx:
			if sord == 'desc':
				order = ' ORDER BY p.%s DESC' %sidx
			else:
				order = ' ORDER BY p.%s ASC ' %sidx
		else:
			order = 'ORDER BY p.id ASC'

		if filters:
			conditions = [];
			filters = loads(filters)
			groupOp = filters['groupOp']
			rules = filters['rules']
			fields = {'id': 'p.id', 'asin':'p.asin', 'category':'c.name', 'username':'u.id', 'alliance':'a.id', 'status':'p.status'}

			for rule in rules:
				conditions.append(fields.get(rule['field']) + '="' +rule['data'] + '"') 

			where = groupOp.center(5).join(conditions)
		else:
			where = '1'

		self.db.execute('SELECT count(*) as total FROM %s p,%s c,alliance a,admin u WHERE p.category=c.id AND p.alliance=a.id \
			AND p.user=u.id AND (%s)' %(self.getOperTableName('product'), self.getOperTableName('category'), where) )
		total = self.db.get_rows(1, is_dict=True)['total']
		paginator = Paginator(rows, total, page, self.request.uri)
		
		self.db.execute('SELECT p.*,c.id as cid,c.name as category,a.name as alliance,u.id as uid,u.name as username FROM %s p,%s c, \
			alliance a,admin u WHERE p.category=c.id AND p.alliance=a.id AND p.user=u.id AND (%s) %s LIMIT %s,%s' \
			%(self.getOperTableName('product'), self.getOperTableName('category'), where, order, paginator.startRow, paginator.stopRows))
		products = self.db.get_rows(is_dict=True)
		
		data['total'] = paginator.totalPages
		data['records'] = len(products)
		data['page'] = page
		data['products'] = products
		callback(data)



class ProductEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			oper = self.get_argument('oper')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.oper, oper)
		self.write(result)
		self.finish()

	def oper(self, args, callback):
		try:
			if args == 'edit':
				keys = ['title', 'price', 'marketPrice', 'view', 'likes', 'score', 'status', 'id']
				arguments = dict()
				for key in keys:
					arguments[key] = self.get_arguments(key)

				print arguments
				for k,v in enumerate(arguments['id']):
					self.db.update(self.getOperTableName('product'), {'title': arguments['title'][k], 'price': arguments['price'][k],\
						'marketPrice': arguments['marketPrice'][k], 'view': arguments['view'][k], 'likes': arguments['likes'][k], \
						'score': arguments['score'][k], 'status': arguments['status'][k]}, 
						{'id': arguments['id'][k]})
					self.lastUpdatedProducts(arguments['id'][k])
			elif args == 'del':
				self.db.execute('SELECT * FROM ' + self.getOperTableName('product')+ ' WHERE id in (%s)' %self.get_argument('id'))
				products = self.db.get_rows(is_dict=True)
				for product in products:
					self.lastUpdatedProducts(product['id'])
					self.db.delete(self.getOperTableName('task'), {'asin': product['asin']})
					self.db.delete(self.getOperTableName('product'), {'id': product['id']})
			else:
				raise ValueError, '错误的操作类型'
 		except Exception:
			callback(traceback.format_exc())
		else:
			callback(u'success')


class ProductAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		alliances = yield gen.Task(self.getAlliances, None)
		categories = yield gen.Task(self.getParentCategories, None)
		self.render('god/productAdd.html', alliances=alliances, categories=categories)

	def getParentCategories(self, args, callback):
		self.db.execute('SELECT * FROM %s WHERE pid=0 AND id!=0' %self.getOperTableName('category'))
		callback(self.db.get_rows(is_dict=True))

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		keys = ['alliance', 'pid','category', 'asin']
		try:
			arguments = dict()
			for key in keys:
				arguments[key] = self.get_arguments(key)
		except Exception:
			self.send_error(500)
			return

		taskId = yield gen.Task(self.addTask, arguments)
		if taskId:
			user = self.getUserInfo()
			response = yield [gen.Task(tasks.addProduct.apply_async, (tid, user['opersite'])) for tid in taskId]
			response = [r.result for r in response]
			if all(response):
				self.render('god/success.html', message=str(len(taskId))+'个任务执行成功', url='/god/products')
			else:
				self.render('god/error.html', message='一些任务执行失败,请查看参数是否填写正确', url='/god/task')
		else:
			self.render('god/error.html', message='没有任务被添加,请检查ASIN号是否重复', url='/god/product/add')

	def addTask(self, args, callback):
		taskId = []
		for k,v in enumerate(args['asin']):
			try:
				self.db.insert(self.getOperTableName('task'), {'pid': args['pid'][k],'category':args['category'][k], 'alliance':args['alliance'][k], \
					'asin':args['asin'][k],'user':self.current_user, 'time': int(time.time())})
				taskId.append(self.db.cursor.lastrowid)
				self.lastUpdatedProducts(args['category'][k], True)
			except Exception:
				print traceback.format_exc()
		callback(taskId)


class TaskHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		categories = yield gen.Task(self.getCategories, None)
		alliances = yield gen.Task(self.getAlliances, None)
		self.render('god/tasks.html', categories=categories, alliances=alliances)


class TasksDataHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		self.set_header('Content-type', 'application/json; charset=utf-8')
		template_variables = yield gen.Task(self.getData, None)
		self.render('god/tasksData.html', **template_variables)		

	def getData(self, args, callback):
		data = dict()
		page = self.get_argument('page', 1)
		rows = self.get_argument('rows', 30)
		sidx = self.get_argument('sidx', '')
		sord = self.get_argument('sord', '')
		filters = self.get_argument('filters', '')

		if sidx:
			if sord == 'desc':
				order = ' ORDER BY t.%s DESC' %sidx
			else:
				order = ' ORDER BY t.%s ASC ' %sidx
		else:
			order = 'ORDER BY t.id ASC'

		if filters:
			conditions = [];
			filters = loads(filters)
			groupOp = filters['groupOp']
			rules = filters['rules']
			fields = {'id': 't.id', 'asin':'t.asin', 'category':'c.id', 'username':'u.id', 'alliance':'a.id', 'status':'t.status'}

			for rule in rules:
				conditions.append(fields.get(rule['field']) + '="' +rule['data'] + '"') 

			where = groupOp.center(5).join(conditions)
		else:
			where = '1'

		self.db.execute('SELECT count(*) as total FROM %s t,%s c,alliance a,admin u WHERE t.category=c.id AND t.alliance=a.id \
			AND t.user=u.id AND (%s)' %(self.getOperTableName('task') , self.getOperTableName('category'),where))
		total = self.db.get_rows(size=1, is_dict=True)['total']
		paginator = Paginator(rows, total, page, self.request.uri)
		
		self.db.execute('SELECT t.id,t.asin,t.status,t.time,c.id as cid,c.name as category,a.name as alliance,u.id as uid,\
			u.name as username FROM %s t,%s c,alliance a,admin u WHERE t.category=c.id AND t.alliance=a.id AND t.user=u.id AND \
			(%s) %s LIMIT %s,%s' %(self.getOperTableName('task'), self.getOperTableName('category') , where, order, paginator.startRow, paginator.stopRows))
		tasks = self.db.get_rows(is_dict=True)
		
		data['total'] = paginator.totalPages
		data['records'] = len(tasks)
		data['page'] = page
		data['tasks'] = tasks
		callback(data)


class TaskEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			oper = self.get_argument('oper')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.oper, oper)
		self.write(result)
		self.finish()

	def oper(self, args, callback):
		try:
			if args == 'edit':
				keys = ['asin', 'status', 'id']
				arguments = dict()
				for key in keys:
					arguments[key] = self.get_arguments(key)
				for k,v in enumerate(arguments['id']):
					self.db.update(self.getOperTableName('task'), {'asin': arguments['asin'][k], 'status': arguments['status'][k]}, \
						{'id': arguments['id'][k]})
			elif args == 'del':
				self.db.execute("DELETE FROM %s WHERE id in (%s)" %(self.getOperTableName('task'), self.get_argument('id')))
			else:
				raise ValueError, '错误的操作类型'
 		except Exception:
			callback(traceback.format_exc())
		else:
			callback(u'success')


class TaskExecuteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		user = self.getUserInfo()
		yield [gen.Task(tasks.addProduct.apply_async, (i, user['opersite'])) for i in id.split(',')]
		self.finish()
		

class AllianceHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		alliances = yield gen.Task(self.getAlliances, None)
		self.render('god/alliances.html', alliances=alliances)


class AllianceDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			pass

		yield gen.Task(self.deleteAlliance, id)
		self.redirect('/god/alliances')

	def deleteAlliance(self, args, callback):
		callback(self.db.delete('alliance', {'id': args}))


class AllianceEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		keys = ['id', 'name', 'domain', 'productUrl']
		try:
			arguments = dict()
			for key in keys:
				arguments[key] = self.get_argument(key)
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.editAlliance, arguments)
		if result == True:
			self.redirect('/god/alliances')
		else:
			self.render('god/error.html', message=result, url='/god/alliances')

	def editAlliance(self, args, callback):
		try:
			self.db.update('alliance', {'name': args['name'], 'domain': args['domain'],\
			'productUrl': args['productUrl']}, {'id': args['id']})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class AllianceAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/allianceAdd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		keys = ['name', 'domain', 'productUrl']
		try:
			arguments = dict()
			for key in keys:
				arguments[key] = self.get_arguments(key)
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.addAlliance, arguments)
		if result == True:
			self.render('god/success.html', url='/god/alliances')
		else:
			self.render('god/error.html', message=result, url='/god/alliances/add')

	def addAlliance(self, args, callback):
		try:
			for k,v in enumerate(args['name']):
				self.db.insert('alliance', {'name': args['name'][k], 'domain': args['domain'][k], \
					'productUrl': args['productUrl'][k]})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class AdminHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		admin = yield gen.Task(self.getData, None)
		self.render('god/admin.html', admin=admin)

	def getData(self, args, callback):
		self.db.execute('SELECT * FROM admin')
		callback(self.db.get_rows(is_dict=True))


class AdminEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine	
	def post(self):
		data = dict()
		try:
			arguments = ['id', 'name', 'email','password', 'power', 'photoName']
			for argument in arguments:
				data[argument] = self.get_argument(argument)
			data['photo'] = self.request.files.get('photo')
		except Exception:
			self.send_error(500)


		result = yield gen.Task(self.editAdmin, data)

		if(result == True):
			self.render('god/success.html', url='/god/admin')
		else:
			self.render('god/error.html', message=result, url='/god/admin')

	def editAdmin(self, args, callback):
		try:
			data = {'name': args['name'], 'email': args['email'], 'power': args['power']}

			if args['photo']:
				k = utils.getPhotoName('admin', args['photo'][0]['filename'].split('.').pop())
				ret, err = qiniu.io.put(self.uptoken, k, data=args['photo'][0]['body'])
				if err is not None:
					raise IOError, err

				ret, err = qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], args['photoName'].split('/').pop())
				newPhotoName = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], k)	
				data['photo'] = newPhotoName

			if args['password']:
				data['password'] = utils.encrypt(args['password'])

			self.db.update('admin', data, {'id': args['id']})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class AdminAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/adminAdd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		result = yield gen.Task(self.addAdmin, None)
		if(result == True):
			self.render('god/success.html', url='/god/admin')
		else:
			self.render('god/error.html', message=result, url='/god/admin/add')

	def addAdmin(self, args, callback):
		try:
			form = AdminAddForm(self)
			if not form.validate():
				raise ValueError, str(form.errors)

			photo = self.request.files.get('photo')
			k = utils.getPhotoName('admin', photo[0]['filename'].split('.').pop())
			ret, err = qiniu.io.put(self.uptoken, k, data=photo[0]['body'])
			if err is not None: raise IOError, err

			photoName = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], k)	
			self.db.insert('admin', {'name': form.name.data, 'password': utils.encrypt(form.password.data),\
				'email': form.email.data, 'photo': photoName,'power': form.power.data})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class AdminDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		result = yield gen.Task(self.deleteAdmin, None)
		if(result == True):
			self.render('god/success.html', url='/god/admin')
		else:
			self.render('god/error.html', message=result, url='/god/admin')

	def deleteAdmin(self, args, callback):
		try:
			id = self.get_argument('id')
			user = self.getUserInfo(userId=id)
			ret, err = qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], user['photo'].split('/').pop())
			self.db.delete('admin', {'id': id})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class ConfigHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		config = yield gen.Task(self.getConfig, None)
		self.render('god/config.html', config=config)


class ConfigAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/configAdd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		result = yield gen.Task(self.addConfig, None)
		if(result == True):
			self.render('god/success.html', url='/god/config')
		else:
			self.render('god/error.html', message=result, url='/god/config/add')

	def addConfig(self, args, callback):
		try:
			arguments = dict()
			keys = ['name', 'value','description']
			for key in keys:
				arguments[key] = self.get_arguments(key)
			for k,v in enumerate(arguments['name']):
				self.db.insert(self.getOperTableName('config'), {'name': arguments['name'][k], 'value': arguments['value'][k],\
					'description': arguments['description'][k]})	
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class ConfigEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		result = yield gen.Task(self.editConfig, None)
		if(result == True):
			self.render('god/success.html', url='/god/config')
		else:
			self.render('god/error.html', message=result, url='/god/config')

	def editConfig(self, args, callback):
		try:
			data = dict()
			arguments = ['name', 'value','description']
			for argument in arguments:
				data[argument] = self.get_argument(argument)
			self.db.update(self.getOperTableName('config'), data, {'id': self.get_argument('id')})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class ConfigDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		result = yield gen.Task(self.deleteConfig, None)
		if(result == True):
			self.render('god/success.html', url='/god/config')
		else:
			self.render('god/error.html', message=result, url='/god/config')

	def deleteConfig(self, args, callback):
		try:
			id = self.get_argument('id')
			self.db.delete(self.getOperTableName('config'), {'id': id})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class PusherHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		pushers = yield gen.Task(self.getPusher, None)
		self.render('god/pusher.html', pushers=pushers)

	def getPusher(self, args, callback):
		self.db.execute("SELECT * FROM %s ORDER BY id desc" %self.getOperTableName('pusher'))
		callback(self.db.get_rows(is_dict=True))


class PushTaskHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		tasks = yield gen.Task(self.getPushTask, None)
		self.render('god/pushTask.html', tasks=tasks)

	def getPushTask(self, args, callback):
		self.db.execute("SELECT * FROM %s ORDER BY ID DESC" %self.getOperTableName('push'))
		callback(self.db.get_rows(is_dict=True))


class PushAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/pushAdd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['product'] = self.get_argument('product')
			args['title'] = self.get_argument('title', None)
			args['keyword'] = self.get_argument('keyword')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.addPush, args)
		if not isinstance(result, int):
			self.render('god/error.html', message=result, url='/god/push/add')

		user = self.getUserInfo()
		response = yield gen.Task(tasks.pushProduct.apply_async, (result, user['opersite']))
		if(response.result):
			self.render('god/success.html', message=u'推送成功', url='/god/push/task')
		else:
			self.render('god/error.html', message=u'推送失败', url='/god/push/add')

	def addPush(self, args, callback):
		try:
			self.db.execute("SELECT p.id, p.title, c.name as category FROM " + self.getOperTableName('product') + " p, " + \
				self.getOperTableName('category')+" c WHERE p.id=%s AND p.pid=c.id LIMIT 1", (args['product'],))
			if self.db.get_rows_num() == 0:
				raise ValueError, '商品编号不存在'
				return

			product = self.db.get_rows(size=1, is_dict=True)
			args['title'] = args['title'] if args['title'] else product['title']

			self.db.insert(self.getOperTableName('push'), {'product': args['product'], 'category': product['category'], \
				'title': args['title'], 'keyword': args['keyword'], 'time': int(time.time())})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(self.db.cursor.lastrowid)


class PushTaskDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.deletePushTask, id)
		if(result == True):
			self.render('god/success.html', message=u'删除成功', url='/god/push/task')
		else:
			self.render('god/error.html', message=result, url='/god/push/task')

	def deletePushTask(self, id, callback):
		try:
			self.db.delete(self.getOperTableName('push'), {'id': id})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class FeedbackHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		tucao = yield gen.Task(self.getData, None)
		self.render('god/feedback.html', tucao=tucao)

	def getData(self, args, callback):
		self.db.execute("SELECT * FROM %s ORDER BY id DESC" %self.getOperTableName('tucao'))
		callback(self.db.get_rows(is_dict=True))


class FBReplyHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return
		data = yield gen.Task(self.getData, id)
		self.render('god/reply.html', data=data)

	def getData(self, id, callback):
		self.db.execute("SELECT * FROM " + self.getOperTableName('tucao') + " WHERE id=%s LIMIT 1", (id,))
		callback(self.db.get_rows(size=1, is_dict=True))

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['id'] = self.get_argument('id')
			args['registration'] = self.get_argument('registration')
			args['msg'] = self.get_argument('msg')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doReply, args)
		if(result == True):
			user = self.getUserInfo()
			yield gen.Task(tasks.pushMessage.apply_async, (args['registration'], user['opersite'])) 
			self.render('god/success.html', message=u'回复成功', url='/god/feedback')
		else:
			self.render('god/error.html', message=result, url='/god/feedback')


	def doReply(self, args, callback):
		try:
			self.db.insert(self.getOperTableName('tucao'), {'uid': self.current_user, 'registration': args['registration'], \
				'msg': args['msg'], 'time': int(time.time()), 'msgtype': 1})
			self.db.update(self.getOperTableName('tucao'), {'`read`': '1'}, {'id': args['id']})
		except Exception:
			callback(traceback.format_exc)
		else:
			callback(True)


class APPHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		apps = yield gen.Task(self.getApp, None)
		self.render('god/app.html', apps=apps)

	def getApp(self, args, callback):
		self.db.execute("SELECT * FROM app")
		callback(self.db.get_rows(is_dict=True))


class APPAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/appadd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['package'] = self.get_argument('package')
			args['versonname'] = self.get_argument('versonname')
			args['description'] = self.get_argument('description')
			args['path'] = self.request.files.get('path')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doAddAPP, args)
		if(result == True):
			self.render('god/success.html', message=u'添加成功', url='/god/app')
		else:
			self.render('god/error.html', message=result, url='/god/app/add')

	def doAddAPP(self, args, callback):
		try:
			self.db.execute("SELECT * FROM app WHERE package=%s AND versonname=%s", 
				(args['package'], args['versonname']))
			
			if self.db.get_rows_num() != 0:
				raise ValueError, "版本号已经存在"

			self.db.execute("SELECT * FROM app WHERE package=%s ORDER BY versoncode DESC LIMIT 1", (args['package'],))
			if self.db.get_rows_num() == 0:
				args['versoncode'] = 1
			else:
				app = self.db.get_rows(size=1, is_dict=True)
				args['versoncode'] = app['versoncode'] + 1

			filename = args['path'][0]['filename']
			ret, err = qiniu.io.put(self.uptoken, filename, data=args['path'][0]['body'])
			if err is not None:
				raise IOError, err

			args['path'] = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], filename)
			args['date'] = int(time.time())

			self.db.insert('app', args)
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class APPDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doAPPDelete, id)
		if(result == True):
			self.render('god/success.html', message=u'删除成功', url='/god/app')
		else:
			self.render('god/error.html', message=result, url='/god/app')		

	def doAPPDelete(self, id, callback):
		try:
			self.db.execute("SELECT * FROM app WHERE id=%s", (id,))
			if self.db.get_rows_num() != 0:
				app = self.db.get_rows(size=1, is_dict=True)
				qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], app['path'].split('/').pop())
			self.db.delete('app', {'id': id})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class APPEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['id'] = self.get_argument('id')
			args['package'] = self.get_argument('package')
			args['versonname'] = self.get_argument('versonname')
			args['versoncode'] = self.get_argument('versoncode')
			args['description'] = self.get_argument('description')
			args['path'] = self.request.files.get('path')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doAPPEdit, args)
		if(result == True):
			self.render('god/success.html', message=u'修改成功', url='/god/app')
		else:
			self.render('god/error.html', message=result, url='/god/app')			

	def doAPPEdit(self, args, callback):
		try:
			self.db.execute("SELECT * FROM app WHERE package=%s AND versonname=%s AND id!=%s", \
				(args['package'], args['versonname'], args['id']))
			if self.db.get_rows_num() != 0:
				raise ValueError, '版本号已经存在'

			self.db.execute("SELECT * FROM app WHERE package=%s AND versoncode=%s AND id!=%s", \
				(args['package'], args['versoncode'], args['id']))

			if self.db.get_rows_num() != 0:
				raise ValueError, '版本码已经存在'

			self.db.execute("SELECT * FROM app WHERE id=%s", (args['id'],))
			if self.db.get_rows_num() == 0:
				raise ValueError, 'id不存在'

			app = self.db.get_rows(size=1, is_dict=True)

			if args['path']:
				qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], app['path'].split('/').pop())

				filename = args['path'][0]['filename']
				ret, err = qiniu.io.put(self.uptoken, filename, data=args['path'][0]['body'])
				if err is not None:
					raise IOError, err
				args['path'] = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], filename)
			else:
				del args['path']

			self.db.update('app', args, {'id': args['id']})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class UserHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/users.html')


class UserDataHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		self.set_header('Content-type', 'application/json; charset=utf-8')
		template_variables = yield gen.Task(self.getUser, None)
		self.render('god/usersData.html', **template_variables)

	def getUser(self, args, callback):
		data = dict()
		page = self.get_argument('page', 1)
		rows = self.get_argument('rows', 30)
		sidx = self.get_argument('sidx', '')
		sord = self.get_argument('sord', '')
		filters = self.get_argument('filters', '')

		if sidx:
			if sord == 'desc':
				order = ' ORDER BY %s DESC' %sidx
			else:
				order = ' ORDER BY %s ASC ' %sidx
		else:
			order = 'ORDER BY id ASC'

		if filters:
			conditions = [];
			filters = loads(filters)
			groupOp = filters['groupOp']
			rules = filters['rules']

			for rule in rules:
				conditions.append(rule['field'] + '="' +rule['data'] + '"') 

			where = groupOp.center(5).join(conditions)
		else:
			where = '1'

		self.db.execute("SELECT * FROM %s WHERE %s " %(self.getOperTableName('user'), where))
		total = self.db.get_rows_num()
		paginator = Paginator(rows, total, page, self.request.uri)
		
		self.db.execute("SELECT * FROM %s WHERE %s %s LIMIT %s,%s" %(self.getOperTableName('user') ,where, order, paginator.startRow, paginator.stopRows))
		users = self.db.get_rows(is_dict=True)
		
		data['total'] = paginator.totalPages
		data['records'] = len(users)
		data['page'] = page
		data['users'] = users
		callback(data)


class UserEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			oper = self.get_argument('oper')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.oper, oper)
		self.write(result)
		self.finish()

	def oper(self, args, callback):
		try:
			if args == 'edit':
				keys = ['nickname', 'email', 'gender', 'id']
				arguments = dict()
				for key in keys:
					arguments[key] = self.get_arguments(key)
				for k,v in enumerate(arguments['id']):
					self.db.update(self.getOperTableName('user'), {'nickname': arguments['nickname'][k], 'email': arguments['email'][k],\
						'gender': arguments['gender'][k]}, {'id': arguments['id'][k]})
			elif args == 'del':
				self.db.execute('SELECT * FROM %s WHERE id in (%s)' %(self.getOperTableName('user'), self.get_argument('id')))
				users = self.db.get_rows(is_dict=True)
				for user in users:
					self.db.delete(self.getOperTableName('user'), {'id': user['id']})
			else:
				raise ValueError, '错误的操作类型'
 		except Exception:
			callback(traceback.format_exc())
		else:
			callback('success')


class SubjectHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		subjects = yield gen.Task(self.getSubject, None)
		self.render('god/subjects.html', subjects=subjects)

	def getSubject(self, args, callback):
		self.db.execute("SELECT * FROM %s ORDER BY id DESC" %self.getOperTableName('subject'))
		callback(self.db.get_rows(is_dict=True))


class SubjectAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/subjectAdd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['title'] = self.get_argument('title')
			args['product'] = self.get_argument('product')
			args['description'] = re.sub('\s', '&nbsp;', self.get_argument('description'))
			args['photo'] = self.request.files.get('photo')
			args['ishow'] = self.get_argument('ishow', 0)
			args['sort'] = self.get_argument('sort')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSubjectAdd, args)
		if(result == True):
			self.render('god/success.html', message=u'添加成功', url='/god/subjects')
		else:
			self.render('god/error.html', message=result, url='/god/subject/add')			


	def doSubjectAdd(self, args, callback):
		try:
			photo = args['photo']
			key = utils.getPhotoName('subject', photo[0]['filename'].split('.').pop())
			ret, err = qiniu.io.put(self.uptoken, key, data=photo[0]['body'])
			if err is not None: 
				raise IOError, err			
			photoName = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], key)

			self.db.insert(self.getOperTableName('subject'), {'title': args['title'], 'description': args['description'], \
				'photo': photoName, 'product': args['product'], 'ishow': args['ishow'], 'sort': args['sort'], 
				'time': int(time.time())})
			subjectId = self.db.cursor.lastrowid
			products = args['product'].split(',')

			for i in products:
				self.db.insert(self.getOperTableName('subitem'), {'subject': subjectId, 'product': i})
			self.lastUpdatedSubjects()
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class SubjectEditHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		subject = yield gen.Task(self.getSubject, id)
		self.render('god/subjectEdit.html', subject=subject)

	def getSubject(self, id, callback):
		self.db.execute("SELECT * FROM " + self.getOperTableName('subject')+ " WHERE id=%s", (id,))
		callback(self.db.get_rows(size=1, is_dict=True))

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['id'] = self.get_argument('id')
			args['title'] = self.get_argument('title')
			args['description'] = re.sub('\s', '&nbsp;', self.get_argument('description'))
			args['product'] = self.get_argument('product')
			args['ishow'] = self.get_argument('ishow', 0)
			args['currentPhoto'] = self.get_argument('currentPhoto')
			args['photo'] = self.request.files.get('photo')
			args['sort'] = self.get_argument('sort')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSubjectEdit, args)
		if(result == True):
			self.render('god/success.html', message=u'编辑成功', url='/god/subjects')
		else:
			self.render('god/error.html', message=result, url=self.referer)

	def doSubjectEdit(self, args, callback):
		try:
			if args['photo']:
				qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], args['currentPhoto'].split('/').pop())

				photo = args['photo']
				key = utils.getPhotoName('subject', photo[0]['filename'].split('.').pop())
				ret, err = qiniu.io.put(self.uptoken, key, data=photo[0]['body'])
				if err is not None: 
					raise IOError, err			
				args['photo'] = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], key)
			else:
				del args['photo']
			
			del args['currentPhoto']
			self.db.update(self.getOperTableName('subject'), args, {'id': args['id']})
			self.db.delete(self.getOperTableName('subitem'), {'subject': args['id']})

			for i in args['product'].split(','):
				self.db.insert(self.getOperTableName('subitem'), {'subject': args['id'], 'product': i})

			self.lastUpdatedSubjects()
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class SubjectDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doSubjectDelete, id)

		if(result == True):
			self.render('god/success.html', message=u'删除成功', url='/god/subjects')
		else:
			self.render('god/error.html', message=result, url='/god/subjects')	

	def doSubjectDelete(self, id, callback):
		try:
			self.db.execute("SELECT * FROM " + self.getOperTableName('subject') + " WHERE id=%s", (id,))
			subject = self.db.get_rows(size=1, is_dict=True)
			qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], subject['photo'].split('/').pop())

			self.db.delete(self.getOperTableName('subject'), {'id': subject['id']})
			self.db.delete(self.getOperTableName('subitem'), {'subject': subject['id']})

			self.lastUpdatedSubjects()
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class AvatarHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		avatars = yield gen.Task(self.getAvatar, None)
		self.render('god/avatar.html', avatars=avatars)

	def getAvatar(self, args, callback):
		self.db.execute("SELECT * FROM avatar ")
		callback(self.db.get_rows(is_dict=True))


class AvatarAddHandler(BaseHandler):
	@authenticated
	@asynchronous
	def get(self):
		self.render('god/avatarAdd.html')

	@authenticated
	@asynchronous
	@gen.coroutine
	def post(self):
		try:
			args = dict()
			args['gender'] = self.get_arguments('gender')
			args['avatar'] = self.request.files.get('avatar')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doAddAvatar, args)
		if(result == True):
			self.render('god/success.html', message=u'添加成功', url='/god/avatar/default')
		else:
			self.render('god/error.html', message=result, url=self.referer)	

	def doAddAvatar(self, args, callback):
		try:
			for key, value in enumerate(args['gender']):
				k = utils.getPhotoName('default_avatar', args['avatar'][key]['filename'].split('.').pop())
				qiniu.io.put(self.uptoken, k, data=args['avatar'][key]['body'])
				photoName = "http://%s.qiniudn.com/%s" % (self.application.settings['qiniu_bucket_name'], k)			
				self.db.insert('avatar', {'gender': args['gender'][key], 'avatar': photoName})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)


class AvatarDeleteHandler(BaseHandler):
	@authenticated
	@asynchronous
	@gen.coroutine
	def get(self):
		try:
			id = self.get_argument('id')
		except Exception:
			self.send_error(500)
			return

		result = yield gen.Task(self.doAvatarDelete, id)
		if(result == True):
			self.render('god/success.html', message=u'删除成功', url='/god/avatar/default')
		else:
			self.render('god/error.html', message=result, url='/god/avatar/default')	

	def doAvatarDelete(self, id, callback):
		try:
			self.db.execute("SELECT * FROM avatar WHERE id=%s AND used=0 LIMIT 1", (id,))
			if self.db.get_rows_num() == 0:
				raise ValueError, "删除失败,头像已经被使用"		

			avatar = self.db.get_rows(size=1, is_dict=True)
			qiniu.rs.Client().delete(self.settings['qiniu_bucket_name'], avatar['avatar'].split('/').pop())
			self.db.delete('avatar', {'id': id})
		except Exception:
			callback(traceback.format_exc())
		else:
			callback(True)