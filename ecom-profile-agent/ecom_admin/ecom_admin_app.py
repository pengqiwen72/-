#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
销售智能体管理后台 Flask 应用
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import os
from dotenv import load_dotenv
from functools import wraps
from ecom_store_api.ecom_store_api import create_sagt_store_api, EcomStoreAPI

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # 在生产环境中应该使用更安全的密钥


# 创建数据客户端
client: EcomStoreAPI = create_sagt_store_api(url = os.getenv('ECOM_SERVER_URL'), user_id = os.getenv('ECOM_USER_ID'), password = os.getenv('ECOM_PASSWORD'))

def login_required(f):
    """登录装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """首页重定向到员工列表"""
    return redirect(url_for('employees'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == os.getenv('ADMIN_USER_ID') and password == os.getenv('ADMIN_PASSWORD'):
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('employees'))
        else:
            return render_template('login.html', error='用户名或密码错误')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """登出"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/employees')
@login_required
def employees():
    """员工列表页"""
    return render_template('employees.html')

@app.route('/employee_customers/<user_id>')
@login_required
def employee_customers(user_id):
    """员工客户列表页"""
    return render_template('employee_customers.html', user_id=user_id)

@app.route('/tags')
@login_required
def tags():
    """标签体系页"""
    return render_template('tags.html')

@app.route('/customer_detail/<follow_user_id>/<external_id>')
@login_required
def customer_detail(follow_user_id, external_id):
    """客户详情页"""
    return render_template('customer_detail.html', follow_user_id=follow_user_id, external_id=external_id)

@app.route('/chat_records/<user_id>/<external_id>')
@login_required
def chat_records(user_id, external_id):
    """对话记录页"""
    return render_template('chat_records.html', user_id=user_id, external_id=external_id)

@app.route('/customer_orders/<union_id>')
@login_required
def customer_orders(union_id):
    """客户订单列表页"""
    return render_template('customer_orders.html', union_id=union_id)

@app.route('/kf_records/<external_id>')
@login_required
def kf_records(external_id):
    """客服记录页"""
    return render_template('kf_records.html', external_id=external_id)

# ==================== API 路由 ====================

@app.route('/api/employees', methods=['GET'])
@login_required
def api_employees():
    """获取员工列表API"""
    try:
        employees = client.list_all_employee()
        return jsonify({"success": True, "data": employees})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/employees', methods=['POST'])
@login_required
def api_create_employee():
    """创建员工API"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        name = data.get('name')
        
        if not user_id or not name:
            return jsonify({"success": False, "error": "用户ID和姓名不能为空"})
        
        client.upsert_employee(user_id, name)
        return jsonify({"success": True, "message": "员工创建成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/employees/<user_id>', methods=['DELETE'])
@login_required
def api_delete_employee(user_id):
    """删除员工API"""
    try:
        ## 暂不允许删除员工
        ## client.delete_employee(user_id)
        return jsonify({"success": True, "message": "暂不允许删除员工"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/employee_customers/<user_id>', methods=['GET'])
@login_required
def api_employee_customers(user_id):
    """获取员工客户列表API"""
    try:
        customers = client.list_external_user_by_follow_user_id(user_id)
        return jsonify({"success": True, "data": customers})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/tags', methods=['GET'])
@login_required
def api_tags():
    """获取标签体系API"""
    try:
        tags = client.list_all_tags_setting(limit=200)
        return jsonify({"success": True, "data": tags})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/customer_detail/<follow_user_id>/<external_id>', methods=['GET'])
@login_required
def api_customer_detail(follow_user_id, external_id):
    """获取客户详情API"""
    try:
        # 获取客户基本信息
        customer = client.get_external_user_by_external_id(external_id, follow_user_id)
        if not customer:
            return jsonify({"success": False, "error": "客户不存在"})
        
        # 获取客户标签信息
        customer_tags = client.get_external_user_tag_by_external_id(external_id, follow_user_id)

        # 获取客户Profile信息
        profile = client.get_profile_by_external_id(external_id, follow_user_id)
        
        return jsonify({
            "success": True,
            "data": {
                "customer": customer,
                "tags": customer_tags,
                "profile": profile
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/chat_records/<user_id>/<external_id>', methods=['GET'])
@login_required
def api_chat_records(user_id, external_id):
    """获取对话记录API"""
    try:
        # 获取查询参数，如果没有提供则使用30天前作为默认值
        after_yyyy_mm_dd = request.args.get('after_yyyy_mm_dd')
        if not after_yyyy_mm_dd:
            from datetime import datetime, timedelta
            # 默认获取30天前的消息
            thirty_days_ago = datetime.now() - timedelta(days=30)
            after_yyyy_mm_dd = thirty_days_ago.strftime('%Y%m%d')
        
        messages = client.list_last_wxqy_msg(external_id, user_id, after_yyyy_mm_dd)
        return jsonify({"success": True, "data": messages})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/customer_orders/<union_id>', methods=['GET'])
@login_required
def api_customer_orders(union_id):
    """获取客户订单API"""
    try:
        orders = client.list_wxxd_order_by_union_id(union_id)
        return jsonify({"success": True, "data": orders})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/kf_records/<external_id>', methods=['GET'])
@login_required
def api_kf_records(external_id):
    """获取客服记录API"""
    try:
        # 获取查询参数，如果没有提供则使用30天前作为默认值
        after_yyyy_mm_dd = request.args.get('after_yyyy_mm_dd')
        if not after_yyyy_mm_dd:
            from datetime import datetime, timedelta
            # 默认获取30天前的消息
            thirty_days_ago = datetime.now() - timedelta(days=30)
            after_yyyy_mm_dd = thirty_days_ago.strftime('%Y%m%d')
        
        messages = client.list_last_wxkf_msg(external_id, after_yyyy_mm_dd)
        return jsonify({"success": True, "data": messages})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', 'False') == 'True', host='0.0.0.0', port=5000) 