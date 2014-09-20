#!/usr/bin/env python
# -*- coding:utf-8 -*-
import re
import time
import datetime
import requests
import json
import smtplib
from email.mime.text import MIMEText
from os import path, makedirs
from lxml import etree
from celery import Celery, platforms
from libs.db import DB
from libs.utils import time2stamp, getTableName
import jpush as jpush

celery = Celery("tasks", broker="amqp://guest:guest@localhost:5672")
celery.conf.CELERY_RESULT_BACKEND = "amqp"
platforms.C_FORCE_ROOT = True

db = DB(True,user='root', password='password',host='localhost', database='shop', \
	unix_socket="/tmp/mysql.sock", buffered=True)
app_key = u'cbc8065f1d0091d9a27a9378'
master_secret = u'ad145d143d70ecb91ec0e193'


def getBuidlerId(sound, vibrate):
	if sound == 1 and vibrate == 0:
		return 1
	elif sound == 0 and vibrate == 1:
		return 2
	else:
		return 3

@celery.task
def pushMessage(registration, operId):
	db.execute("SELECT * FROM " + getTableName('pusher', operId)+ " WHERE registration=%s AND `status`=1 LIMIT 1", (registration,))
	if db.get_rows_num() == 0:
		return

	pusher = db.get_rows(size=1, is_dict=True)
	_jpush = jpush.JPush(app_key, master_secret)
	push = _jpush.create_push()	
	push.audience = jpush.audience(
	    jpush.registration_id(registration)
	)

	push.notification = jpush.notification(
	   android=jpush.android(
	      alert=u'你的吐槽信息有了新的回复~',
	      builder_id=getBuidlerId(pusher['sound'], pusher['vibrate']),
	      extras={'target': 'tucao'}
	   )
	)
	push.platform = jpush.platform('android')
	push.send()

@celery.task
def pushProduct(id, operId):
	try:
		db.execute("SELECT * FROM " + getTableName('push', operId)+ " WHERE id=%s LIMIT 1", (id,))
		msg = db.get_rows(size=1, is_dict=True)

		db.execute("SELECT * FROM %s WHERE `status`=1" %getTableName('pusher', operId))
		pusher = db.get_rows(is_dict=True)

		_jpush = jpush.JPush(app_key, master_secret)
		push = _jpush.create_push()
		total = 0

		for vo in pusher:
			if vo['categories'] and msg['category'] not in vo['categories']:
				continue

			if vo['keyword'] and msg['keyword'] not in vo['keyword']:
				continue

			push.audience = jpush.audience(
	        	jpush.registration_id(vo['registration'])
		        )

			push.notification = jpush.notification(
			   android=jpush.android(
			      alert=msg['title'],
			      builder_id=getBuidlerId(vo['sound'], vo['vibrate']),
			      extras={"id": msg['product'], 'target': 'product'}
			   )
			)
			push.platform = jpush.platform('android')
			push.send()

			total = total + 1
			db.update(getTableName('pusher', operId), {'date': time2stamp(datetime.date.today().strftime('%Y-%m-%d'))}, 
				{'id': vo['id']})
	except Exception:
		db.update(getTableName('push', operId), {'status': 2}, {'id': id})
		return False
	else:
		db.update(getTableName('push', operId), {'status': 1, 'total': total}, {'id': id})
		return True

@celery.task
def addProduct(id, operId):
	data = dict()

	try:
		db.execute("SELECT * FROM " + getTableName('task', operId)+ " WHERE id=%s LIMIT 1", (id,))
		task = db.get_rows(size=1, is_dict=True)
		db.execute("SELECT * FROM alliance WHERE id=%s", (task['alliance'],))
		alliance = db.get_rows(size=1, is_dict=True)
	except Exception:
		db.update(getTableName('task', operId), {'status': '1'}, {'id': id}) #获取任务信息出错
		return False

	try:
		response = requests.get(alliance['productUrl']+'&asins='+task['asin'], timeout=15)
	except Exception:
		db.update(getTableName('task', operId), {'status': '2'}, {'id': id}) #访问推广页面时 网络访问超时
		return False

	try:
		htmlElement = etree.HTML(response.content)
		data['user'] = task['user']
		data['pid'] = task['pid']
		data['category'] = task['category']
		data['alliance'] = task['alliance']
		data['asin'] = task['asin']
		data['url'] = htmlElement.find('.//div[@id="image"]/a').get('href')
		data['photo'] = htmlElement.find('.//div[@id="image"]/a/img').get('src')
		data['price'] = htmlElement.find('.//span[@class="price"]').text
		data['time'] = int(time.time())
	except Exception:
		db.update(getTableName('task', operId), {'status': '3'}, {'id': id}) #获取基本信息出错
		return False		

	try:
		dirName = './html/'
		response = requests.get(data['url'], timeout=15)
		if not path.exists(dirName):
			makedirs(dirName)
		handler = open(dirName+str(task['asin']+'.html'), 'w')
		handler.write(response.content)
		handler.close()
	except Exception:
		db.update(getTableName('task', operId), {'status': '4'}, {'id': id}) #访问商品目标页面时 网络访问超时
		return False

	try:
		htmlElement = etree.HTML(response.content)
		data['title'] = htmlElement.find('.//span[@id="productTitle"]').text
		if data['title'] is None:
			data['title'] = htmlElement.find('.//title').text
			if data['title'] is None:
				raise Exception
	except Exception:
		db.update(getTableName('task', operId), {'status': '5'}, {'id': id}) #获取商品名称出错
		return False

	try:
		data['marketPrice'] = htmlElement.xpath(".//td[text()='"+u'目录价:'+"']/following-sibling::td")[0].text
 	except Exception:
 		search = re.search(r'\d{1,}\.{1}\d{1,}', data['price'])
 		if search is not None:
 			data['marketPrice'] = '￥'+str(float(search.group(0))*0.9)
 		else:
 			data['marketPrice'] = data['price']

	try:
		data['photo'] = htmlElement.find(".//div[@id='imgTagWrapperId']/img").get('src')
	except Exception:
		reg = r'<div class=\\\\"main-image-inner-wrapper\\\\">\\\\n\s*?<img src=\\\\"(.*?)\\\\"'
		search = re.search(reg, response.content)
		if  search is not None:
			data['photo'] = search.group(1)
		else:
			db.update(getTableName('task', operId), {'status': '6'}, {'id': id}) #获取不到商品图片
			return False

	try:
		shotResponse = requests.post('http://dwz.cn/create.php', data={'url': data['url']})
		shotResponse = json.loads(shotResponse.content)
		if(shotResponse.get('status') != 0):
			raise Exception
		data['shortUrl'] = shotResponse.get('tinyurl')
	except Exception:
		db.update(getTableName('task', operId), {'status': '7'}, {'id': id}) #生成短网址失败
		return False

	try:
		scoreTitle = htmlElement.find(".//span[@id='acrPopover']").get('title')
		data['score'] = re.search(r'\d\.\d', scoreTitle).group(0)
	except Exception:
		data['score'] = 5.0

	# try:
	# 	doc = unquote(re.findall(r'var iframeContent =\s*?"([\s|\S]*?)";',response.content)[0])
	# 	htmlElement = etree.HTML(doc)
	# 	data['description'] = html.tostring(htmlElement.xpath(".//div[@class='productDescriptionWrapper']")[0])
	# except Exception:
	# 	pass

	try:
		db.execute("SELECT * FROM " + getTableName('product', operId) + " WHERE asin=%s", (data['asin'],))
		if db.get_rows_num() != 0:
			db.update(getTableName('product', operId), data, {'asin': data['asin']})
		else:
			db.insert(getTableName('product', operId), data)
			
			try:
				gallery = re.findall(r'"large":"([^,]*?)","main', response.content)
				if not gallery:
					gallery = re.findall(r'"hiResImage":"(.*?)",', response.content)

				if gallery:
					productId = db.cursor.lastrowid
					for g in gallery:
						db.insert(getTableName('gallery', operId), {'product': productId, 'gallery': g})
			except Exception:
				pass
	except Exception:
		db.update(getTableName('task', operId), {'status': 8}, {'id': id}) #写入数据库失败
		return False

	db.update(getTableName('task', operId), {'status': 0}, {'id': id})
	return True

@celery.task
def sendMail(toaddrs, subjtct, msg):
	try:
		fromaddr = 'support@oneggo.com'
		
		#使用MIMEText构造符合smtp协议的header及body
		msg = MIMEText(msg)  
		msg["Subject"] = subjtct
		msg["From"]    = fromaddr  
		msg["To"]      = toaddrs
		  
		server = smtplib.SMTP('localhost')
		server.set_debuglevel(1)
		server.sendmail(fromaddr, toaddrs, msg.as_string())
		server.quit()
	except Exception:
		return False
	else:
		return True

if __name__ == "__main__":
    celery.start()