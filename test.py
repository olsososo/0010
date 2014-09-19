#!/usr/bin/env python
# -*- coding:utf-8 -*-
import sys
reload(sys)
sys.setdefaultencoding('utf8')

import requests
import re
from urllib import unquote
from lxml.html.clean import clean_html

def main():
	#r = requests.get('http://www.amazon.cn/mn/detailApp?asin=B00BWL5KRY&tag=no1-23&camp=404&creative=2024&linkCode=as1&creativeASIN=B00BWL5KRY&adid=1R9A56Y5DGGJMESZF1ZR&', timeout=10)
	r = requests.get('http://www.amazon.cn/TP-LINK-TL-WR842N-300M%E6%97%A0%E7%BA%BF%E8%B7%AF%E7%94%B1%E5%99%A8/dp/B00BWL5KRY/ref=sr_1_cc_1?s=aps&ie=UTF8&qid=1402238557&sr=1-1-catcorr&keywords=B00BWL5KRY', timeout=10)

	filename= "D:/Python27/code/shop/a.html"
	f = open(filename,'wb')
	f.write(r.content)
	f.close()

	text = open(filename,'rb').read()

	from lxml import etree
	from lxml import html
	htmlElement = etree.HTML(text);
	price = htmlElement.xpath(".//td[text()='"+u'目录价:'+"']/following-sibling::td")[0].text
	print re.search(r'\d{1,}\.{1}\d{1,}', price).group(0)

	scoreTitle = htmlElement.find(".//span[@id='acrPopover']").get('title') #score
	print re.search(r'\d\.\d', scoreTitle).group(0)

	# print htmlElement.find(".//div[@id='imgTagWrapperId']/img").get('src')
	# gallery = re.findall(r'"large":"([^,]*?)","main', text)
	# if not gallery:
	# 	print re.findall(r'"hiResImage":"([^,]*?)",', text)
	# else:
	# 	print gallery

	# print re.search(r'<div class=\\\\"main-image-inner-wrapper\\\\">\\\\n\s*?<img src=\\\\"(.*?)\\\\"', text).group(1)

	# doc = unquote(re.findall(r'var iframeContent =\s*?"([\s|\S]*?)";',text)[0])
	# htmlElement = etree.HTML(doc)
	# print html.tostring(htmlElement.xpath(".//div[@class='productDescriptionWrapper']")[0])

	#print re.findall(r'<b class="priceLarge">￥ ([\s|\S]*?)<\/b>',text)[0]

	# soup = BeautifulSoup(open("D:/Python27/code/shop/a.html").read())
	# for s in  soup.findAll('span'):
	# 	try:
	# 		print s['class']
	# 	except Exception:
	# 		pass
	# f = open("D:/Python27/code\shop/templates/god/res.txt",'w')
	# for i in soup.findAll('img'):
	# 	f.write(i['src']+"\r\n")
	# f.close()



def mail():
    import smtplib  
    from email.mime.text import MIMEText  
    _user = "442091317@qq.com"  
    _pwd  = "q1257831117"  
    _to   = "442091317@qq.com"  
      
    #使用MIMEText构造符合smtp协议的header及body  
    msg = MIMEText("乔装打扮，不择手段")  
    msg["Subject"] = "don't panic"  
    msg["From"]    = _user  
    msg["To"]      = _to  
      
    s = smtplib.SMTP("smtp.qq.com", timeout=30)#连接smtp邮件服务器,端口默认是25  
    s.login(_user, _pwd)#登陆服务器  
    s.sendmail(_user, _to, msg.as_string())#发送邮件  
    s.close() 


def test():
	import smtplib
	from email.mime.text import MIMEText

	fromaddr = "support@oneggo.com"
	toaddrs  = "442091317@qq.com"

	# Add the From: and To: headers at the start!
	msg = MIMEText("乔装打扮，不择手段")
	msg['Subject'] = "don't panic"
	msg['From'] = fromaddr
	msg['To'] = toaddrs

	server = smtplib.SMTP('localhost')
	server.set_debuglevel(1)
	server.sendmail(fromaddr, toaddrs, msg.as_string())
	server.quit()

if __name__ == '__main__':
	test()


#特价:http://www.amazon.cn/ThinkPad-X240-20AL001-GCD-12-5%E8%8B%B1%E5%AF%B8%E7%AC%94%E8%AE%B0%E6%9C%AC%E7%94%B5%E8%84%91/dp/B00HNMM0HY/ref=lp_106200071_1_4?s=pc&ie=UTF8&qid=1402225028&sr=1-4
