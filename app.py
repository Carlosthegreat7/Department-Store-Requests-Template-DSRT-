from flask import session, jsonify, request, render_template, redirect, url_for, flash
from portal import app, loggedin_required
from extensions import db
from models import Vendor, Brand, SubClass
from datetime import datetime, timedelta, date
import ldap
import pyodbc

# --- BLUEPRINT IMPORTS (routes folder)---
from routes.vendor import vendor_bp
from routes.hierarchy import hierarchy_bp
from routes.subclass import subclass_bp
from routes.transactions import transactions_bp

# --- DATABASE CONFIGURATION ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/myproject'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize DB with App
db.init_app(app)

# --- REGISTER BLUEPRINTS ---
app.register_blueprint(vendor_bp)
app.register_blueprint(hierarchy_bp)
app.register_blueprint(subclass_bp)
app.register_blueprint(transactions_bp)

# --- HELPERS ---
def generate_earliest_missing_date(days):
    return (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

# --- CORE ROUTES (LDAP & AUTH) ---

@app.route('/statuschk', methods=['GET', 'POST'])
def statuschk():
    return jsonify("Site is OK")

@app.route('/', methods=['GET', 'POST'])
def index():
    rule = request.url_rule
    if request.method == 'POST' and 'username' in request.form and 'password' in request.form:
        username = request.form['username']
        password = request.form['password']
        
        conn = ldap.initialize(app.config['LDAP_PROVIDER_URL'])
        
        try:
            # Connect to existing portal user database
            MIS_SysDev_connect = pyodbc.connect(app.config['MIS_SysDev'] + "app=" + rule.rule)
            MIS_SysDev_cursor = MIS_SysDev_connect.cursor()

            sql = 'SELECT username, email, active, role, dept FROM portal_users WHERE username=?'
            user = MIS_SysDev_cursor.execute(sql, (username)).fetchall()

            # Verify active status and bind with LDAP
            if user and user[0][2] == 1:
                try:
                    conn.simple_bind_s("MGROUP\\" + username, password)
                    session.update({
                        'sdr_curr_user_username': user[0][0].upper(),
                        'sdr_curr_user_role': user[0][3],
                        'sdr_loggedin': True,
                        'sdr_usertype': 'Head Office'
                    })
                    return redirect(url_for('index', _external=True))
                except ldap.INVALID_CREDENTIALS:
                    flash("Invalid Domain Login")
            else:
                flash("Login failed or deactivated")
        except Exception as e:
            flash(f"Error: {e}")
        finally:
            if 'MIS_SysDev_cursor' in locals(): MIS_SysDev_cursor.close()
            if 'MIS_SysDev_connect' in locals(): MIS_SysDev_connect.close()
            
    return render_template('home.html')

@app.route('/logout')
@loggedin_required()
def logout():
    session.clear()
    return redirect(url_for('index', _external=True))

# --- MASTER ADMIN MANAGEMENT ---

@app.route('/admin/management', methods=['GET'])
@loggedin_required()
def admin_management():
    return render_template('admin_management.html', 
                           vendors=Vendor.query.all(), 
                           # Sorted by: Brand -> Group -> Dept -> SubDept -> Class
                           hierarchies=Brand.query.order_by(
                               Brand.brand_name, 
                               Brand.product_group, 
                               Brand.dept_code, 
                               Brand.sub_dept_code, 
                               Brand.class_code
                           ).all(), 
                           subclasses=SubClass.query.all())

if __name__ == '__main__':
    # Use this context only once if you need to create tables automatically
    # with app.app_context():
    #     db.create_all()
    app.run(debug=True)

# CarlosTheGreat was here