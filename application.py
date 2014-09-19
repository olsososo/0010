#!/usr/bin/env python
# -*- coding:utf-8 -*-
import sys 
reload(sys)
sys.setdefaultencoding('gbk')

import os.path
import tornado.httpserver
import tornado.ioloop
import tornado.options
import tornado.web
import memcache
import qiniu.conf
import qiniu.rs

import handler.api as api
import handler.god as god
import handler.index as index
import handler.snacks as snacks
from libs.db import DB

from tornado.options import define, options
define('port', default=8080, help='run on the given port', type=int)
define('mysql_host', default = '127.0.0.1', help = 'community database host')
define('mysql_database', default = 'shop', help = 'community database name')
define('mysql_user', default = 'root', help = 'community database user')
define('mysql_password', default = 'password',help = 'community database password')


class Application(tornado.web.Application):
	def __init__(self):
		handlers = [
			(r'/', index.IndexHandler),
			(r'/test', index.TestHandler),

			(r'/api/categories', api.CategoriesHandler),
			(r'/api/products', api.ProductsHandler),
			(r'/api/record', api.RecordHandler),
			(r'/api/login', api.LoginHandler),
			(r'/api/register', api.RegisterHandler),
			(r'/api/oauth', api.OauthHandler),
			(r'/api/likes', api.LikesHandler),
			(r'/api/push', api.PushHandler),
			(r'/api/product', api.ProductHandler),
			(r'/api/send', api.SendHandler),
			(r'/api/msg', api.MsgHandler),
			(r'/api/mail', api.SendMailHandler),
			(r'/api/verify', api.VerifyHandler),
			(r'/api/forgetpassword', api.ForgetPasswordHandler),
			(r'/api/photo', api.SetPhotoHandler),
			(r'/api/check', api.CheckUpdateHandler),
			(r'/api/setting', api.SettingHandler),
			(r'/api/setpassword', api.SetPasswordHandler),
			(r'/api/subject', api.SubjectHandler),
			(r'/api/subjectitem', api.SubjectItemHandler),
			(r'/api/islike', api.IsLikeHandler),

			(r'/god/login', god.LoginHandler),
			(r'/god/logout', god.LogoutHandler),
			(r'/god/main', god.MainHandler),

			(r'/god/categories', god.CategoriesHandler),
			(r'/god/category/delete', god.categoryDeleteHandler),
			(r'/god/category/add', god.CategoryAddHnadler),
			(r'/god/category/edit', god.CategoryEditHandler),
			(r'/god/categoriesData', god.CategoriesDataHandler),

			(r'/god/products', god.ProductsHandler),
			(r'/god/productsData', god.ProductsDataHandler),
			(r'/god/product/edit', god.ProductEditHandler),			
			(r'/god/product/add', god.ProductAddHandler),

			(r'/god/tasks', god.TaskHandler),
			(r'/god/tasksData', god.TasksDataHandler),
			(r'/god/task/edit', god.TaskEditHandler),
			(r'/god/task/execute', god.TaskExecuteHandler),

			(r'/god/alliances', god.AllianceHandler),
			(r'/god/alliance/delete', god.AllianceDeleteHandler),
			(r'/god/alliance/edit', god.AllianceEditHandler),
			(r'/god/alliance/add', god.AllianceAddHandler),

			(r'/god/admin', god.AdminHandler),
			(r'/god/admin/edit', god.AdminEditHandler),
			(r'/god/admin/add', god.AdminAddHandler),
			(r'/god/admin/delete', god.AdminDeleteHandler),

			(r'/god/users', god.UserHandler),
			(r'/god/usersData', god.UserDataHandler),
			(r'/god/user/edit', god.UserEditHandler),

			(r'/god/pusher', god.PusherHandler),
			(r'/god/push/task', god.PushTaskHandler),
			(r'/god/push/add', god.PushAddHandler),
			(r'/god/push/delete', god.PushTaskDeleteHandler),

			(r'/god/feedback', god.FeedbackHandler),
			(r'/god/feedback/reply', god.FBReplyHandler),

			(r'/god/app', god.APPHandler),
			(r'/god/app/add', god.APPAddHandler),
			(r'/god/app/edit', god.APPEditHandler),
			(r'/god/app/delete', god.APPDeleteHandler),

			(r'/god/config', god.ConfigHandler),
			(r'/god/config/add', god.ConfigAddHandler),
			(r'/god/config/edit', god.ConfigEditHandler),
			(r'/god/config/delete', god.ConfigDeleteHandler),

			(r'/god/subjects', god.SubjectHandler),
			(r'/god/subject/add', god.SubjectAddHandler),
			(r'/god/subject/edit', god.SubjectEditHandler),
			(r'/god/subject/delete', god.SubjectDeleteHandler),

			(r'/god/avatar/default', god.AvatarHandler),
			(r'/god/avatar/add', god.AvatarAddHandler),
			(r'/god/avatar/delete', god.AvatarDeleteHandler),
		]

		settings = dict(
			debug=True,
			captcha = False,
			gzip=True,
			autoescape = None,
			xsrf_cookies = True,
			login_url = '/god/login',
			template_path = os.path.join(os.path.dirname(__file__), 'templates'),
			static_path = os.path.join(os.path.dirname(__file__), 'static'),
			cookie_secret = 'bZJc2sWbQLKos6GkHn/VB9oXwYOYO8S0R0kRvJ5/xJ89E=',
			memcached_address = ['127.0.0.1:11211'],
			recaptcha_publickey = '6LdrmfQSAAAAANsIhRkgWyMUWMWEY65tIN9cnqd4',
			recaptcha_privatekey = '6LdrmfQSAAAAAK_j1aPcQJdtVZOXQTk1qNOl6CUd',
			qiniu_access_key = 'iLybixkJH8oTQ8I6_DSSHCSuYNztoQu-YKFratlh',
			qiniu_secret_key = 'a6atbwzXgLjEW3mgoUKJgck1o0mH9U1gx1NzaIvJ',
			qiniu_bucket_name = 'shopa',
		)

		self.db = DB(True, 
			user=options.mysql_user, password=options.mysql_password,
			host=options.mysql_host, database=options.mysql_database,
			unix_socket="/tmp/mysql.sock", buffered=True
		)

		self.mc = memcache.Client(['127.0.0.1:11211'])

		qiniu.conf.ACCESS_KEY = settings['qiniu_access_key']
		qiniu.conf.SECRET_KEY = settings['qiniu_secret_key']

		super(Application, self).__init__(handlers, **settings)

		sub_handlers = [
			["^0010.oneggo.com$", 
				[
					(r'/', snacks.IndexHandler),
				]	
			]
		]

		for sub_handler in sub_handlers:
			self.add_handlers(sub_handler[0], sub_handler[1])

if __name__ == '__main__': 
    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(Application())
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.instance().start()
