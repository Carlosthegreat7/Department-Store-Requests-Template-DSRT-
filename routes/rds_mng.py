from flask import Blueprint, render_template, session, redirect, url_for, flash
from portal import loggedin_required
from models import VendorRDS, HierarchyRDS, PricePointRDS, AgeCodeRDS

# Define the blueprint
rds_mng_bp = Blueprint('rds_mng', __name__)

@rds_mng_bp.route('/admin/management/rds', methods=['GET'])
@loggedin_required()
def admin_management_rds():
    # Security Check
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))
    
    # Fetch all RDS data from the database
    vendors_rds = VendorRDS.query.all()
    hierarchies_rds = HierarchyRDS.query.all()
    price_points_rds = PricePointRDS.query.all()
    age_codes_rds = AgeCodeRDS.query.all()
    
    return render_template('admin_management_rds.html', 
                           vendors=vendors_rds,
                           hierarchies=hierarchies_rds,
                           price_points=price_points_rds,
                           age_codes=age_codes_rds)