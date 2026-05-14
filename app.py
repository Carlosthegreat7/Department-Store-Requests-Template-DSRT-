from flask import session, jsonify, request, render_template, redirect, url_for, flash
from portal import app, loggedin_required
from extensions import db
from models import Vendor, Brand, SubClass, VendorRDS, HierarchyRDS, PricePointRDS, AgeCodeRDS, AuditLog
from datetime import datetime, timedelta, date
import ldap
import pymysql
import pyodbc

# --- BLUEPRINT IMPORTS (routes folder)---
from routes.vendor import vendor_bp
from routes.hierarchy import hierarchy_bp
from routes.subclass import subclass_bp
from routes.transactions import transactions_bp
from routes.rds_mng import rds_mng_bp

# --- DATABASE CONFIGURATION ---
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/myproject'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# app.config['SQLALCHEMY_DATABASE_URI'] = 'mssql+pyodbc://username:password@MGSVR14/myproject?driver=ODBC+Driver+17+for+SQL+Server'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mssql+pyodbc://MGSVR14/DSRT?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes&Trusted_Connection=yes'

# Initialize DB with App
db.init_app(app)

# --- REGISTER BLUEPRINTS ---
app.register_blueprint(vendor_bp)
app.register_blueprint(hierarchy_bp)
app.register_blueprint(subclass_bp)
app.register_blueprint(transactions_bp)
app.register_blueprint(rds_mng_bp)

# --- HELPERS ---
def generate_earliest_missing_date(days):
    return (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

def log_user_action(action_description):
    """Helper function to record an action to the AuditLog."""
    username = session.get('sdr_curr_user_username', 'Unknown/System')
    new_log = AuditLog(user=username, action=action_description, timestamp=datetime.now())
    db.session.add(new_log)
    db.session.commit()

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
            # Connect to user database
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
                    
                    # Log the successful login
                    log_user_action("User successfully logged in")
                    
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
    log_user_action("User logged out")
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

# --- AUDIT LOG ROUTE ---

@app.route('/admin/audit', methods=['GET'])
@loggedin_required()
# @admin_required()  <-- Strongly recommended to implement a role check like this
def audit_tab():
    # 1. Check admin privileges (if you don't have a decorator for it)
    # if not current_user.is_admin:
    #     abort(403) 

    # 2. Get the current page from the URL query string (default is page 1)
    page = request.args.get('page', 1, type=int)
    
    # 3. Paginate the query instead of fetching .all()
    # Adjust per_page to whatever fits your UI best
    pagination = AuditLog.query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=50, error_out=False
    )
    
    # 4. Pass the items and the pagination object to the template
    return render_template(
        'audit_log.html', 
        logs=pagination.items, 
        pagination=pagination
    )

if __name__ == '__main__':
    import os
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=is_debug)
    # Use environment variables to control debug mode instead of hardcoding True
    import os
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    app.run(debug=is_debug)

# CarlosTheGreat was here :)
