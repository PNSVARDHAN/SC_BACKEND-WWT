from flask import Blueprint, redirect, url_for, render_template, request
from extensions import db
import datetime

bp = Blueprint('main', __name__)

