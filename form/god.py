#!/usr/bin/env python
# -*- coding:utf-8 -*-

from libs.forms import Form
from wtforms import TextField, PasswordField, validators, IntegerField

class LoginForm(Form):
    name = TextField('name',[
        validators.Required(message = u"Username must be filled"),
    ])

    password = PasswordField('password',[
        validators.Required(message = u"Must enter a password"),
    ])	


class AdminAddForm(Form):
    name = TextField('name',[
        validators.Required(message = u"Username must be filled"),
        validators.Length(max = 16,message = u"User name can not be longer than 16 characters"),
        validators.Regexp("^[a-zA-Z0-9]{1,16}$",message = u"Please use the half-width of a-z or 0-9")
    ])

    password = PasswordField('password',[
        validators.Required(message = u"Must enter a password"),
        validators.Length(min = 6,message = u"Password length is too short (6-16 characters)"),
        validators.Length(max = 16,message = u"Password length is too long (6-16 characters)")
    ])

    email = TextField('email',[
        validators.Length(min = 6,message = u"Email slightly shorter length"),
        validators.Email(message = u"E-mail format is incorrect")
    ])

    power = IntegerField('power',[
        validators.required(message = u"Account must fill level"),
    ])